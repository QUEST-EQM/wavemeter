from typing import List, Any
from hf_wavemeter.hw.HighFinesse_dll import HFDLL
from ctypes import (
    WINFUNCTYPE,
    POINTER,
    cast,
    c_short,
    c_int,
    c_long,
    c_double,
    pointer,
    c_void_p
)

import time

import asyncio
import janus
from threading import Lock

from sipyco.pc_rpc import simple_server_loop
from sipyco.common_args import simple_network_args, bind_address_from_args, verbosity_args, init_logger_from_args
from sipyco.sync_struct import Publisher, Notifier

from argparse import ArgumentParser

import logging
logger = logging.getLogger(__name__)


class WavemeterServer:
    """
    Server handling the communication with the wavemeter software.

    All wavelength values in here are vacuum wavelengths in nm.
    Timestamps are in ms.

    The main functionality is to install a callback function and provide notifiers for all channels.
    Each notifier publishes a list with a single entry, which is a tuple of the form `(timestamp, value)`.

    The notifiers are called 'ch<n>', where n is the channel number (starting at 1).
    The latest known value can also be obtained via :meth:`get_wavelength()`.

    Optionally, the same functionality is available for temperature and pressure.
    Notifiers are called 'T' and 'p', respectively. Units are degrees Celsius and mbar.
    Latest known values can also be obtained via :meth:`get_temperature()` and :meth:`get_pressure()`.

    Besides readout, control functions are provided via RPCs and the current status is reported via the
    notifier `status`.

    An autocalibration feature is available. It counts down, checks that a given channel is within some given range
    around the expected value (i.e. the laser is locked to the correct mode of a cavity) before stopping the wavelength
    measurement, performing a calibration, and then restarting the measurement. The countdown value and the
    time stamp of the last successful calibration (via this feature) are published via the notifier `status` as entries
    `autocal_countdown` and `calibration_timestamp`.

    :param channels: list of channel ids to read out
    :param num_hw_channels: number of channels available in the hardware
    :param get_temperature: enable temperature readout
    :param get_pressure: enable pressure readout
    :param skip_threshold_nm: values are ignored if they differ by more than this from the previous one
                              (to discard the occasional value from a wrong channel)
    :param install_callback: start wavemeter readout immediately
    :param start_publisher: start the publisher server
    :param publisher_host: host for the publisher server
    :param publisher_port: port for the publisher server
    :param event_loop: asyncio event loop to use for handling new values (defaults to asyncio.get_event_loop())
    """

    def __init__(self, channels: List[int] = None, num_hw_channels: int = 8,
                 get_temperature: bool = False, get_pressure: bool = False, skip_threshold_nm: float = 10.,
                 install_callback: bool = True, start_publisher: bool = True, publisher_host: str = "*",
                 publisher_port: int = 3281, event_loop: Any = None):
        self.hf_dll = HFDLL()
        channels = channels if channels is not None else [i for i in range(1, 9)]
        self._channels = list(set(["ch{}".format(c) for c in channels]))
        self._get_temperature = get_temperature
        self._get_pressure = get_pressure

        self._mode_dict = dict()
        for k, v in self.hf_dll.const.items():
            if k[0:13] == "cmiWavelength" and int(k[13:]) <= num_hw_channels:
                self._mode_dict.update({v: "ch{}".format(k[13:])})
        if self._get_temperature:
            self._mode_dict.update({self.hf_dll.const["cmiTemperature"]: "T"})
        if self._get_pressure:
            self._mode_dict.update({self.hf_dll.const["cmiPressure"]: "p"})

        self._update_valid_modes()

        self._skip_threshold_nm = skip_threshold_nm

        self._callback_installed = False
        self._wavemeter_callback_type = WINFUNCTYPE(c_int, c_long, c_int, c_double)
        self._wavemeter_callback_object = self._wavemeter_callback_type(self._wavemeter_callback)

        self.wm_status_dict = {"autocal_countdown": 0, "calibration_timestamp": -1}
        self.status_notifier = Notifier(self.wm_status_dict)

        self._autocal_running = False
        self._stop_autocal = True
        self._autocal_timestamp = -1
        self._autocal_retry_interval = 10

        self.latest_data = {n: [(0, -1.)] for n in self._mode_dict.values()}
        # since latest_data can be an error code, store last valid value separately
        # (avoids jump detection getting triggered after each error)
        self._latest_valid_value = {n: -1. for n in self._mode_dict.values()}

        self.notifiers = {n: Notifier(self.latest_data[n]) for n in self._mode_dict.values()}
        self.notifiers.update({"status": self.status_notifier})
        self.publisher = Publisher(self.notifiers)
        self._publisher_host = publisher_host
        self._publisher_port = publisher_port

        # one (thread safe) lock per channel to prevent values from stacking up (discard new while channel is busy)
        self._locks = {n: Lock() for n in self._mode_dict.values()}
        self._queue = None  # Janus uses asyncio.get_current_loop() as of version 0.5
        # -> Queue needs to be created from within _new_value_loop

        # interferogram export variables (index is 0 or 1 for the two different interferometers)
        self._pattern_export_enabled = [False, False]  # stores whether export has been enabled
        self._pattern_item_type = [None, None]  # data type (ctypes class)
        self._pattern_item_count = [-1, -1]  # number of data points

        self._event_loop = event_loop if event_loop is not None else asyncio.get_event_loop()
        self._event_loop.create_task(self._new_value_loop())
        if install_callback:
            self.install_wavemeter_callback()
        if start_publisher:
            self._event_loop.create_task(self.start_publisher())

    def _update_valid_modes(self):
        """
        Updates the list of wavemeter message modes to process (channel wavelengths, temperature, pressure),
        based on the list of channels and whether temperature and/or pressure are read out.
        """
        temp = []
        for k in self._mode_dict.keys():  # add channel wavelengths
            if self._mode_dict[k] in self._channels:
                temp.append(k)
        if self._get_temperature:
            temp.append(self.hf_dll.const["cmiTemperature"])
        if self._get_pressure:
            temp.append(self.hf_dll.const["cmiPressure"])
        self._valid_modes = temp

    def _wavemeter_callback(self, mode: int, intval: int, dblval: float) -> int:
        """
        Callback function to get wavemeter updates.
        If a previous value for the same channel is still being processed, the new one is dropped.

        This runs in a separate thread (handled by ctypes). Thread-safe transfer of the new values to the
        asyncio tasks is ensured by the use of a janus queue.

        :param mode: used to determine whether message is a wavelength, and if so, from which channel
        :param intval: time stamp in ms
        :param dblval: vacuum wavelength in nm, temperature in  degrees C or pressure in mbar
        """
        if mode in self._valid_modes:
            if self._locks[self._mode_dict[mode]].acquire(blocking=False):
                self._queue.sync_q.put_nowait((self._mode_dict[mode], intval, dblval))
            else:
                logger.warning("Value dropped: {}, timestamp {}, value {}, queue size {}".format(
                    self._mode_dict[mode], intval, dblval, self._queue.sync_q.qsize()))
        return 0

    async def _process_wm_event(self, channel: str, timestamp: int, value: float):
        """
        If the callback determines a wavemeter event to be of interest, it is handled here.
        All values arriving while this is running are discarded since they are likely to be outdated by the time
        they would be processed.

        :param channel: wavemeter channel 'ch<n>', 'T' or 'p'
        :param timestamp: timestamp  provided by the wavemeter
        :param value: vacuum wavelength in nm, temperature in degrees C or pressure in mbar
        """
        if value > 0:
            if (abs(value - self._latest_valid_value[channel]) < self._skip_threshold_nm  # check for wl jump
                    or self._latest_valid_value[channel] == -1  # ignore jump check on initial value
                    or channel in ["T", "p"]):
                self.notifiers[channel][0] = (timestamp, value)
                self._latest_valid_value[channel] = value
            else:
                logger.warning("{} value ignored due to {} nm jump".format(channel,
                                                                           value - self.latest_data[channel][0][1]))
                # still update the internal variable, in case the value before the jump was the glitch
                self._latest_valid_value[channel] = value
        else:  # error code - still publish to allow clients to deal with errors
            try:
                error = self.hf_dll.Err[value]
            except KeyError:
                error = "error code {}".format(value)
            logger.warning("{} error: {}".format(channel, error))
            self.notifiers[channel][0] = (timestamp, value)

        self._locks[channel].release()

    async def _new_value_loop(self):
        """
        Receives new values via self._queue and passes them on to :meth:`_process_wm_event`.
        """
        if self._queue is None:
            self._queue = janus.Queue()
        while True:
            channel, timestamp, value = await self._queue.async_q.get()
            self._event_loop.create_task(self._process_wm_event(channel, timestamp, value))

    async def start_publisher(self):
        """
        Start the publisher for channel notifiers.
        """
        await self.publisher.start(self._publisher_host, self._publisher_port)

    def install_wavemeter_callback(self):
        """
        Installs the callback function which receives value updates from the wavemeter software.
        """
        if self._callback_installed:
            return
        logger.debug("installing wavemeter callback")
        self.hf_dll.Instantiate(self.hf_dll.const["cInstNotification"], self.hf_dll.const["cNotifyInstallCallback"],
                                cast(self._wavemeter_callback_object, POINTER(c_long)), 0)

        self._callback_installed = True

    def remove_wavemeter_callback(self):
        """
        Removes the wavemeter callback.
        """
        if not self._callback_installed:
            return
        logger.debug("removing wavemeter callback")
        self.hf_dll.Instantiate(self.hf_dll.const["cInstNotification"],
                                self.hf_dll.const["cNotifyRemoveCallback"], cast(None, POINTER(c_long)), 0)

        self._callback_installed = False

    async def add_channel(self, channel: int):
        """
        Add a channel to the list of monitored ones.

        :param channel: channel number
        """
        channel_name = "ch{}".format(channel)
        if channel_name not in self._channels:
            reinstall_callback = False
            if self._callback_installed:
                reinstall_callback = True
                self.remove_wavemeter_callback()
            self._channels.append(channel_name)
            self._update_valid_modes()
            if reinstall_callback:
                self.install_wavemeter_callback()

    async def remove_channel(self, channel: int):
        """
        Stop monitoring a channel.

        :param channel: channel number
        """
        channel_name = "ch{}".format(channel)
        reinstall_callback = False
        if self._callback_installed:
            reinstall_callback = True
            self.remove_wavemeter_callback()
        try:
            self._channels.pop(self._channels.index(channel_name))
        except ValueError:
            pass
        self._update_valid_modes()
        if reinstall_callback:
            self.install_wavemeter_callback()

    def get_active_channels(self) -> List[int]:
        """
        Get the list of channels currently monitored.

        :return: channel list
        """
        return [int(ch[2:]) for ch in self._channels]

    def get_wavelength(self, channel: int) -> float:
        """
        Returns the latest known wavelength of a channel.

        :param channel: channel number
        :return: vacuum wavelength in nm
        """
        return self.latest_data["ch{}".format(channel)][0][1]

    def get_temperature(self) -> float:
        """
        Returns the wavemeter temperature
        (only available if server is constructed with get_temperature=True).

        :return: temperature in degrees C
        """
        return self.latest_data["T"][0][1] if self._get_temperature else -1

    def get_pressure(self) -> float:
        """
        Returns the wavemeter pressure
        (only available if server is constructed with get_pressure=True).

        :return: pressure in mbar
        """
        return self.latest_data["p"][0][1] if self._get_pressure else -1

    # Wavelength calibration functions
    def calibrate(self, channel: int, wavelength: float) -> int:
        """Wavelength calibration.

        :param channel: calibration channel id
        :param wavelength: calibration vacuum wavelength in nm
        :return: error code of the wavemeter software function `Calibration`
        """
        logger.info("Attempting calibration (channel: {} measured wl: {} nm, calibration wl: {} nm)".format(
            channel, self.latest_data["ch{}".format(channel)][0][1], wavelength))
        self.hf_dll.Operation(self.hf_dll.const["cCtrlStopAll"])
        ret = self.hf_dll.Calibration(self.hf_dll.const["cOther"], self.hf_dll.const["cReturnWavelengthVac"],
                                      wavelength, channel)
        self.hf_dll.Operation(self.hf_dll.const["cCtrlStartMeasurement"])
        if ret == 0:
            self.status_notifier["calibration_timestamp"] = time.time()
        else:
            logger.warning("Wavemeter software returned {} on calibration attempt".format(self.hf_dll.ResErr[ret]))
        return ret

    async def _calibration_loop(self, channel: int, wavelength: float, threshold: float, interval: int,
                                retry_interval: int):
        """
        Loop which decrements the calibration countdown and performs calibrations. See :meth:`start_autocalibration`
        for parameter descriptions.
        """
        self._autocal_running = True
        while not self._stop_autocal:
            if self.wm_status_dict["autocal_countdown"] <= 0:
                if abs(self.latest_data["ch{}".format(channel)][0][1] - wavelength) < threshold:
                    self.calibrate(channel, wavelength)
                    self.status_notifier["autocal_countdown"] = interval
                else:
                    logger.warning("Suspending autocalibration (channel: {} measured wl: {} nm, calibration wl: {} nm)."
                                   " Retrying in {} s".format(channel,
                                                              self.latest_data["ch{}".format(channel)][0][1],
                                                              wavelength, retry_interval))
                    self.status_notifier["autocal_countdown"] = retry_interval
            await asyncio.sleep(1)
            self.status_notifier["autocal_countdown"] = self.wm_status_dict["autocal_countdown"] - 1
        self._autocal_running = False

    async def start_autocalibration(self, channel: int, wavelength: float, threshold: float = 0.00005,
                                    interval: int = 600, retry_interval: int = 10):
        """
        Start autocalibration.

        :param channel: calibration channel id
        :param wavelength: calibration vacuum wavelength in nm
        :param threshold: only calibrate if the actual wavelength is within this distance (in nm)
                          of the calibration wavelength
        :param interval: calibration interval in seconds
        :param retry_interval: time in seconds before the next attempt after a calibration has been skipped due to
                               the threshold check (note: if the wavemeter software returns an error, the delay
                               before the next attempt is `interval` to avoid frequent interruptions)
        """
        # Wait for running instance to terminate (if any)
        self._stop_autocal = True
        while self._autocal_running:
            await asyncio.sleep(1)

        self._stop_autocal = False
        self.status_notifier["autocal_countdown"] = 0
        self._event_loop.create_task(self._calibration_loop(channel, wavelength, threshold, interval, retry_interval))

    def stop_autocalibration(self):
        """Stop the autocalibration."""
        self._stop_autocal = True

    def get_time_since_calibration(self) -> float:
        """
        Returns the time since the last successful autocalibration.

        :return: time in seconds
        """
        return time.time() - self.wm_status_dict["calibration_timestamp"]

    def start_measurement(self):
        """Starts the wavemeter measurement."""
        self.hf_dll.Operation(self.hf_dll.const["cCtrlStartMeasurement"])

    def stop_measurement(self):
        """Stops the wavemeter measurement."""
        self.hf_dll.Operation(self.hf_dll.const["cCtrlStopAll"])

    def get_exposure_auto_adjust(self, channel: int) -> bool:
        """
        Returns whether automatic exposure time adjustment is enabled.

        :param channel: the channel for which to check
        :return: auto exposure state
        """
        return self.hf_dll.GetExposureModeNum(channel, False)

    def set_exposure_auto_adjust(self, channel: int, state: bool) -> int:
        """
        Enables / disables automatic exposure time adjustment.

        :param channel: channel id
        :param state: auto exposure state
        :return: error code of the wavemeter software function `SetExposureModeNum`
        """
        return self.hf_dll.SetExposureModeNum(channel, state)

    def get_exposure_time(self, channel: int, ccd_array: int) -> int:
        """
        Reads out the exposure time of a give channel and CCD array.

        :param channel: channel id
        :param ccd_array: CCD array id (1 or 2; array 2 is exposed for the sum of both values)
        :return: exposure time in ms
        """
        return self.hf_dll.GetExposureNum(channel, ccd_array, 0)

    def set_exposure_time(self, channel: int, ccd_array: int, time: int) -> int:
        """
        Sets the exposure time of a give channel and CCD array.

        :param channel: channel id
        :param ccd_array: CCD array id (1 or 2; array 2 is exposed for the sum of both values)
        :param time: exposure time in ms
        :return: error code of the wavemeter software function `SetExposureNum`
        """
        return self.hf_dll.SetExposureNum(channel, ccd_array, time)

    def get_interferogram(self, channel: int, index: int) -> List[int]:
        """
        Returns the interferogram data.

        :param channel: wavemeter channel
        :param index: interferometer index (0 or 1)
        :return: pixel values
        """
        if index not in [0, 1]:
            raise Exception("Unsupported interferometer index. Use either 0 or 1.")

        hf_index = self.hf_dll.const["cSignal1Interferometers"] if index == 0\
            else self.hf_dll.const["cSignal1WideInterferometer"]

        if not self._pattern_export_enabled[index]:
            ret = self.hf_dll.SetPattern(hf_index, 1)
            if ret == 0:
                self._pattern_export_enabled[index] = True
            else:
                raise Exception("Call to SetPattern({}, 1) returned {}".format(hf_index, self.hf_dll.ResErr[ret]))
        if self._pattern_item_count[index] == -1:
            self._pattern_item_count[index] = self.hf_dll.GetPatternItemCount(hf_index)
        if self._pattern_item_type[index] is None:
            item_size = self.hf_dll.GetPatternItemSize(hf_index)
            if item_size == 2:
                self._pattern_item_type[index] = c_short
            elif item_size == 4:
                self._pattern_item_type[index] = c_long
            elif item_size == 8:
                self._pattern_item_type[index] = c_double
            else:
                raise Exception("Unexpected data type for interferogram values.")

        array = (self._pattern_item_type[index] * self._pattern_item_count[index])()
        self.hf_dll.GetPatternDataNum(channel, hf_index, cast(pointer(array), c_void_p))

        return list(array)

    def disable_interferogram_export(self, index: int):
        """
        Disable export of interferograms (enabled by :meth:`get_interferogram`).
        Can potentially improve the performance of the HF software.

        :param index: interferometer index (0 or 1)
        """
        if index not in [0, 1]:
            return
        hf_index = self.hf_dll.const["cSignal1Interferometers"] if index == 0\
            else self.hf_dll.const["cSignal1WideInterferometer"]
        if self._pattern_export_enabled[index]:
            ret = self.hf_dll.SetPattern(hf_index, 0)
            if ret == 0:
                self._pattern_export_enabled[index] = False
            else:
                raise Exception("Call to SetPattern({}, 0) returned {}".format(hf_index, self.hf_dll.ResErr[ret]))

    def ping(self) -> bool:
        """Can be used by RPC clients to prevent connection timeouts"""
        return True

    # Expose bare dll function calls
    def ControlWLM(self, Action, App, Ver):
        """Wavemeter DLL function"""
        return self.hf_dll.ControlWLM(Action, App, Ver)

    def Operation(self, Op):
        """Wavemeter DLL function"""
        return self.hf_dll.Operation(Op)

    def GetSwitcherMode(self, SM):
        """Wavemeter DLL function"""
        return self.hf_dll.GetSwitcherMode(SM)

    def SetSwitcherMode(self, SM):
        """Wavemeter DLL function"""
        return self.hf_dll.SetSwitcherMode(SM)


def get_argparser():
    parser = ArgumentParser(description="Wavemeter server")
    group = parser.add_argument_group("data readout")
    group.add_argument("-c", "--channel", nargs="*", default=[i for i in range(1, 9)],
                       help="list of channels to read out (default: 1 2 3 4 5 6 7 8)")
    group.add_argument("--nchannels", default=8, help="number of hardware channels (default: 8)")
    parser.set_defaults(T=False, p=False)
    group.add_argument("-T", dest="T", action="store_true",
                       help="enable temperature readout (available as channel \"T\")")
    group.add_argument("-p", dest="p", action="store_true",
                       help="enable pressure readout (available as channel \"p\")")
    group.add_argument("-skip-thr", default=10., help="values are ignored if they differ by more than this (in nm) from"
                                                      " the previous reading of the same channel (this discards the"
                                                      " occasional values from a wrong channel) (default: 10.0)")
    group = parser.add_argument_group("startup options")
    group.add_argument("--callback", default=True, help="install the wavemeter software callback at startup"
                                                        " (default: True)")
    group.add_argument("--start-pub", default=True, help="start the publisher (for new values and status updates)"
                                                         " at startup (default: True)")
    simple_network_args(parser, [("rpc", "RPC", 3280), ("pub", "publisher (for new values and status updates)", 3281)])
    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    channels = []
    for ch in args.channel:
        try:
            channels.append(int(ch))
        except ValueError:
            pass
    init_logger_from_args(args)
    simple_server_loop({"wavemeter_server": WavemeterServer(channels=channels, num_hw_channels=args.nchannels,
                                                            get_temperature=args.T, get_pressure=args.p,
                                                            skip_threshold_nm=args.skip_thr,
                                                            install_callback=args.callback,
                                                            start_publisher=args.start_pub,
                                                            publisher_host=bind_address_from_args(args),
                                                            publisher_port=args.port_pub)},
                       bind_address_from_args(args), args.port_rpc)


if __name__ == "__main__":
    main()
