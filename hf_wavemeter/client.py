from typing import Any

from sipyco.sync_struct import Notifier, Subscriber
from sipyco.common_args import simple_network_args

import asyncio

import logging
logger = logging.getLogger(__name__)


class WavemeterClient:
    """
    Base class for wavemeter clients. Installs a subscriber for one channel (wavelength, temperature or pressure).
    Values are accessible via `self.value`, corresponding timestamps from the wavemeter software via `self.timestamp`.

    Wavelengths are vacuum wavelengths in nm, temperature is in degrees C, pressure is in mbar.

    :meth:`_new_value_callback` gets called on every update (`value` and `timestamp` already return the new
    values at the time of the call).

    :param channel: wavemeter channel to subscribe to (can be either <n>, '<n>', 'ch<n>', 'T' or 'p')
    :param host: host running the publisher
    :param port: port of the publisher
    :param event_loop: asyncio event loop for starting the subscriber (defaults to asyncio.get_event_loop())

    """
    def __init__(self, channel: str = "ch1", host: str = "::1", port: int = 3281, event_loop: Any = None):
        try:  # accept integer (or string lacking the "ch" prefix) as channel argument
            self.channel = "ch{}".format(int(channel))
        except ValueError:
            self.channel = channel
        self.host = host
        self.port = port

        self.data = [(0, -1.)]
        self._subscriber = Subscriber(self.channel, self._subscriber_init_cb, self._subscriber_mod_cb)

        self._event_loop = event_loop if event_loop is not None else asyncio.get_event_loop()

        self._event_loop.create_task(self._subscriber.connect(self.host, self.port))

    def _subscriber_init_cb(self, data):
        self.data = data
        self._init_callback()
        return data

    def _subscriber_mod_cb(self, _):
        self._new_value_callback()

    @property
    def value(self) -> float:
        """
        Latest value.

        :return: latest known value (vacuum wavelength in nm, temperature in degrees C, or pressure in mbar)
        """
        return self.data[0][1]

    @property
    def timestamp(self) -> int:
        """
        Latest time stamp.

        :return: timestamp (from the wavemeter software) for latest known value in ms
        """
        return self.data[0][0]

    def _init_callback(self):
        pass

    def _new_value_callback(self):
        pass

    def get_channel(self) -> str:
        """The wavemeter channel to which this client has subscribed.

        :return: channel name
        """
        return self.channel

    def close_subscriber(self):
        self._event_loop.run_until_complete(self._subscriber.close())


class WavemeterPIClient(WavemeterClient):
    """
    Base class for a PI lock wavemeter client. Also provides scanning functionality.
    Can be used as an RPC target and its member `notifier` can be used to monitor status variables
    (no publisher provided).

    Actual implementations need to override :meth:`_set_output` by an appropriate hardware function.
    Also consider overriding :meth:`get_output_unit`, e.g. to allow the GUI to display the correct units.

    An auxiliary output is available by overriding :meth:`set_aux_output` (and :meth:`get_aux_output_unit` and
    :meth:`get_aux_output_name`), e.g. to allow manual adjustments to the current of an ECDL from the remote GUI.

    :meth:`_output_railing_alert` can be overridden to issue warnings if the output gets too close to one of its limits.

    :param channel: wavemeter channel to use as input (of the form 'ch<n>' [or 'T' or 'p' if you want to
                    feed back on those...])
    :param output_min: lower limit for the output
    :param output_max: upper limit for the output
    :param warning_margin: :meth:`_output_railing_alert` is called and the status variable `output_rail_warning`
                           is set to True whenever the output value gets within this fractional distance of a limit
    :param integrator_timeout: if the time between updates exceeds this value (in ms),
                               the integrator is not updated
    :param cp: proportional gain
    :param ci: integrator gain
    :param setpoint: setpoint in nm
    :param output_sensitivity: sensitivity of the output in nm / <output unit>, used for integrator feed forward
    :param output_offset: offset added to the output
    :param integrator_cutoff: if the signal is within this distance (in nm) of the setpoint, don't update the
                              integrator (used for relock systems where this component mustn't compete with the main
                              control loop unless things go wrong)
    :param startup_locked: enable lock immediately
    :param output_min: lower limit for the auxiliary output
    :param output_max: upper limit for the auxiliary output
    """
    def __init__(self, output_min: float = -10., output_max: float = 10., warning_margin: float = 0.05,
                 integrator_timeout: int = 10000, cp: float = 0., ci: float = 0., setpoint: float = -1.,
                 output_sensitivity: float = 0., output_offset: float = 0., integrator_cutoff: float = 0.,
                 startup_locked: bool = False, aux_output_min: float = -10., aux_output_max: float = 10., **kwargs):
        super().__init__(**kwargs)
        self.output_min = output_min
        self.output_max = output_max
        self.output_high_alert = output_max - warning_margin * (output_max - output_min)
        self.output_low_alert = output_min + warning_margin * (output_max - output_min)
        self.integrator_timeout = integrator_timeout
        self.integrator_cutoff = integrator_cutoff

        self.aux_output_min = aux_output_min
        self.aux_output_max = aux_output_max

        if output_offset < output_min or output_offset > output_max:
            logger.warning("Output offset ({}) outside output range ({} < output < {})".format(output_offset,
                                                                                               output_min,
                                                                                               output_max))

        self._status_dict = {
            "latest_value": 0.,
            "latest_timestamp": -1,
            "locked": False,
            "setpoint": setpoint,
            "setpoint_noscan": setpoint,
            "cp": cp,
            "ci": ci,
            "integrator": 0.,
            "output": 0.,
            "output_offset": output_offset,
            "output_rail_warning": False,
            "aux_output": 0.,
            "scanning": False,
            "output_sensitivity": output_sensitivity
        }

        self.status_notifier = Notifier(self._status_dict)

        self._previous_timestamp = - integrator_timeout  # ensure first value is ignored by the integrator
        self._previous_value = 0.

        # lock to keep values from piling up - new values are dropped if the previous update isn't complete
        self._asyncio_lock = asyncio.Lock()

        self._sensitivity_measurement_running = False
        self._sensitivity_measurement_avg_sum = 0
        self._sensitivity_measurement_avg_numvalues = 0

        if startup_locked:
            self.relock()

    def _set(self, key: str, value: Any):
        self.status_notifier[key] = value

    def get(self, key: str) -> Any:
        """
        Return a value from the status dictionary.

        :param key: dictionary key (see `_status_dict`)
        :return: value
        """
        return self._status_dict[key]

    def _new_value_callback(self):
        if self.get("locked"):
            self._event_loop.create_task(self._lock_update(self.value, self.timestamp))

        self._set("latest_timestamp", self.timestamp)
        self._set("latest_value", self.value)

        if self._sensitivity_measurement_running:
            self._sensitivity_measurement_avg_sum += self.value
            self._sensitivity_measurement_avg_numvalues += 1

    async def _lock_update(self, value: float, timestamp: int):
        if value <= 0:  # ignore error codes
            return

        if self._asyncio_lock.locked():
            return

        await self._asyncio_lock.acquire()

        # update integrator
        if (timestamp - self._previous_timestamp) <= self.integrator_timeout\
                and abs(0.5 * (value + self._previous_value) - self.get("setpoint")) > self.integrator_cutoff:
            self._set("integrator", self.get("integrator") + self.get("ci") *
                      (timestamp - self._previous_timestamp) *
                      (0.5 * (value + self._previous_value) - self.get("setpoint")))

        await self.set_output(self.get("output_offset") + self.get("cp") * (value - self.get("setpoint"))
                              + self.get("integrator"))

        self._previous_value = value
        self._previous_timestamp = timestamp

        self._asyncio_lock.release()

    async def _output_railing_alert(self):
        pass

    async def set_output(self, output_value: float) -> float:
        """
        Set the output value. Applies output limits and issues a warning when the margin is low.

        This method gets called by the servo, but can also be used as an RPC. Mixing both might be a bad idea though.

        :param output_value: value to apply at the output
        :return: actually applied value
        """
        # apply limits
        output_value = output_value if output_value < self.output_max else self.output_max
        output_value = output_value if output_value > self.output_min else self.output_min

        actual_output_value = await self._set_output(output_value)

        self._set("output", actual_output_value)

        if actual_output_value < self.output_low_alert or actual_output_value > self.output_high_alert:
            self._event_loop.create_task(self._output_railing_alert())
            if not self.get("output_rail_warning"):
                logger.warning("{} lock client output rail warning ({} {})".format(self.channel, actual_output_value,
                                                                                   self.get_output_unit()))
                self._set("output_rail_warning", True)
        elif self.get("output_rail_warning"):
            self._set("output_rail_warning", False)

        return actual_output_value

    async def _set_output(self, output_val: float) -> float:
        """Actual hardware output function. Needs to return the actual value set at the output."""
        raise NotImplementedError

    def get_output_unit(self) -> str:
        """Returns the unit of the output."""
        return ""

    async def set_aux_output(self, output_val: float) -> float:
        """Set the auxiliary output."""
        output_val = output_val if output_val < self.aux_output_max else self.aux_output_max
        output_val = output_val if output_val > self.aux_output_min else self.aux_output_min
        actual_value = await self._set_aux_output(output_val)
        self._set("aux_output", actual_value)
        return actual_value

    async def _set_aux_output(self, output_val: float) -> float:
        """Actual hardware output function. Needs to return the actual value set at the output."""
        return 0.

    def get_aux_output_unit(self) -> str:
        """Returns the unit of the auxiliary output."""
        return ""

    def get_aux_output_name(self) -> str:
        """Returns the name of the auxiliary output."""
        return "N/A"

    def lock(self, setpoint: float, cp: float = 0., ci: float = 0.):
        """
        Engages lock (or updates lock parameters).
        The output is cp * (lambda_i - setpoint) + ci * sum_i [(t_i - t_{i-1}) * (lambda_i + lambda_{i-1}) / 2]
        (=> you want negative cp, ci values for negative feedback unless there are further inversions).

        :param setpoint: lock setpoint in nm
        :param cp: proportional gain
        :param ci: integrator gain
        """
        prev_setpoint = self.get("setpoint")
        self._set("scanning", False)
        self._set("setpoint", setpoint)
        self._set("setpoint_noscan", setpoint)

        self._set("cp", cp)
        self._set("ci", ci)

        if self.get("locked"):
            self._integrator_ff(setpoint - prev_setpoint)
        self._set("locked", True)

    def relock(self):
        """
        (Re-)engages lock with the previous values. Aborts scans.
        If the setpoint is altered by an aborted scan, this resets it.
        """
        self.lock(self.get("setpoint_noscan"), self.get("cp"), self.get("ci"))

    def unlock(self):
        """
        Stop the feedback.
        """
        self._set("locked", False)
        self._set("scanning", False)

    def reset_integrator(self):
        """Resets the integrator."""
        self._set("integrator", 0.)

    def change_lock_setpoint(self, setpoint: float):
        """
        Change the lock setpoint.

        :param setpoint: new setpoint value in nm
        """
        previous_setpoint = self.get("setpoint_noscan")
        logger.info("Changing lock setpoint to {} nm".format(setpoint))
        self._set("setpoint_noscan", setpoint)

        if not self.get("scanning"):
            self._set("setpoint", self.get("setpoint_noscan"))

        # feed forward to integrator and update output
        if self.get("locked"):
            self._integrator_ff(setpoint - previous_setpoint)

    def setpoint_step_mhz(self, step: float):
        """
        Changes the setpoint by a given amount.

        :param step: change in MHz
        """
        self.change_lock_setpoint(self.get("setpoint") - self.get("setpoint")**2 / 299792458e3 * step)

    def measure_output_sensitivity(self, lower_output_value: float, upper_output_value: float, averaging_time: float,
                                   settle_time: float = 2):
        """
        Determines the slope between output value and wavelength by observing the wavelength at two output values.
        The result is used to feed forward to the integrator when the setpoint is changed, manually or within a scan.
        Obviously, this method cannot run while locked.

        :param lower_output_value: lower output value
        :param upper_output_value: upper output value
        :param averaging_time: averaging time at each value in seconds
        :param settle_time: settling time between output change and measurement in seconds
        """
        self._event_loop.create_task(self._measure_output_sensitivity(lower_output_value, upper_output_value,
                                                                      averaging_time, settle_time))

    def set_output_offset(self, offset):
        """
        Change the output offset (effective from the next lock update).

        :param offset: new value
        """
        self._set("output_offset", offset)

    def _integrator_ff(self, step):
        """
        If the output sensitivity is known, adjust the integrator value to a setpoint jump.

        :param step: setpoint jump in nm
        """
        if self.get("output_sensitivity") != 0:
            logger.info("Feeding forward to the integrator: step of {} nm => {} "
                        "{}".format(step, step / self.get("output_sensitivity"), self.get_output_unit()))
            self._set("integrator", self.get("integrator") + step / self.get("output_sensitivity"))
            self._event_loop.create_task(self.set_output(self.get("integrator")))

    async def _measure_output_sensitivity(self, lower_output_value: float, upper_output_value: float,
                                          averaging_time: float, settle_time: float):
        if self.get("locked"):
            return

        logger.info("Determining output sensitivity: lower value: {} {}, upper value: {} {},"
                    " averaging_time: {} s".format(lower_output_value, self.get_output_unit(), upper_output_value,
                                                   self.get_output_unit(), averaging_time))
        previous_output_value = self.get("output")

        # enforce limits
        lower_output_value = lower_output_value if lower_output_value < self.output_max else self.output_max
        lower_output_value = lower_output_value if lower_output_value > self.output_min else self.output_min
        upper_output_value = upper_output_value if upper_output_value < self.output_max else self.output_max
        upper_output_value = upper_output_value if upper_output_value > self.output_min else self.output_min

        await self.set_output(lower_output_value)
        await asyncio.sleep(settle_time)

        self._sensitivity_measurement_avg_sum = 0.
        self._sensitivity_measurement_avg_numvalues = 0
        self._sensitivity_measurement_running = True
        await asyncio.sleep(averaging_time)

        self._sensitivity_measurement_running = False
        lower_average = self._sensitivity_measurement_avg_sum / self._sensitivity_measurement_avg_numvalues

        await self.set_output(upper_output_value)
        await asyncio.sleep(settle_time)

        self._sensitivity_measurement_avg_sum = 0.
        self._sensitivity_measurement_avg_numvalues = 0
        self._sensitivity_measurement_running = True
        await asyncio.sleep(averaging_time)

        self._sensitivity_measurement_running = False
        upper_average = self._sensitivity_measurement_avg_sum / self._sensitivity_measurement_avg_numvalues

        await self.set_output(previous_output_value)

        self._set("output_sensitivity", (upper_average - lower_average) / (upper_output_value - lower_output_value))
        logger.info("Determined output sensitivity: {} nm / {}".format(self.get("output_sensitivity"),
                                                                       self.get_output_unit()))

    def start_scan(self, waveform: int = 0, scan_rate: float = 10., lower_frequency: float = 0.,
                   upper_frequency: float = 0., timestep: float = 0.1):
        r"""
        Scans the lock setpoint relative to its initial value as set by :meth:`lock`, :meth:`relock`,
        :meth:`change_lock_setpoint` or :meth:`setpoint_step_mhz`.
        Feeds forward to the integrator and output if the slope has been determined via
        :meth:`measure_output_sensitivity` (advisable to avoid overshoots with the sawtooth waveforms).

        :param waveform: scan waveform. One of:

                          ============= ===========================================================
                           waveform id   shape
                          ============= ===========================================================
                           0 		     ramp from current frequency to initial setpoint
                           1 		     ramp from current to lower frequency
                           2 	   	     ramp from current to upper frequency
                           3 	   	     triangle starting upward in frequency /\/\/\...
                           4 		     triangle starting downward in frequency \/\/\/...
                           5 		     sawtooth: upward, starting with jump to min |/|/|/...
                           6 	   	     sawtooth: upward, starting with ramp to max /|/|/|...
                           7             sawtooth: downward, starting with jump to max |\|\|\...
                           8 	         sawtooth: downward, starting with ramp to min \|\|\|...
                          ============= ===========================================================

        :param lower_frequency: lower frequency offset in MHz
        :param upper_frequency: upper frequency offset in MHz
        :param scan_rate: scan rate in MHz/s
        :param timestep: time in seconds between steps
        """
        self._event_loop.create_task(self._scan(waveform, scan_rate, lower_frequency, upper_frequency, timestep))

    async def _scan(self, waveform: int, scan_rate: float, lower_frequency: float, upper_frequency: float,
                    timestep: float):
        if not self.get("locked"):
            raise Exception("Scanning only works in lock")
        if self.get("scanning"):
            raise Exception("Already scanning")
        if timestep <= 0.:
            raise Exception("Please choose a nonzero, positive time step")
        if lower_frequency > upper_frequency:
            raise Exception("Upper frequency should be higher than lower frequency")

        self._set("scanning", True)
        logger.info("Starting scan: waveform: {}, scan_rate: {} MHz / s, lower freq {} MHz, upper freq {} MHz, "
                    "time step {} s".format(waveform, scan_rate, lower_frequency, upper_frequency, timestep))

        # convert frequency offsets and step size to wavelengths
        lower_freq_wavelength = 299792458e3 / (299792458e3 / self.get("setpoint_noscan") + lower_frequency)
        upper_freq_wavelength = 299792458e3 / (299792458e3 / self.get("setpoint_noscan") + upper_frequency)
        wavelength_step = self.get("setpoint_noscan")**2 / 299792458e3 * scan_rate * timestep
        direction = 0

        # initialize
        if waveform == 3:  # triangle starting up (in frequency)
            direction = -1

        if waveform == 4:  # triangle starting down (in frequency)
            direction = 1

        if waveform == 5:  # sawtooth |/ (in frequency)
            self._set("setpoint", lower_freq_wavelength)
            self._integrator_ff(lower_freq_wavelength - self.get("setpoint_noscan"))
            direction = -1

        if waveform == 6:  # sawtooth /| (in frequency)
            direction = -1

        if waveform == 7:  # sawtooth |\ (in frequency)
            self._set("setpoint", upper_freq_wavelength)
            self._integrator_ff(upper_freq_wavelength - self.get("setpoint_noscan"))
            direction = 1

        if waveform == 8:  # sawtooth \| (in frequency)
            direction = 1

        #  scan
        while self.get("scanning"):
            previous_setpoint = self.get("setpoint")

            if waveform == 0:  # ramp to initial setpoint
                direction = 1 if self.get("setpoint") < self.get("setpoint_noscan") else -1
                if abs(self.get("setpoint") - self.get("setpoint_noscan")) > wavelength_step:
                    self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                else:
                    self._set("setpoint", self.get("setpoint_noscan"))
                    self._set("scanning", False)

            if waveform == 1:  # ramp to lower_frequency
                direction = 1 if self.get("setpoint") < lower_freq_wavelength else -1
                if abs(self.get("setpoint") - lower_freq_wavelength) > wavelength_step:
                    self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                else:
                    self._set("setpoint", lower_freq_wavelength)
                    self._set("scanning", False)

            if waveform == 2:  # ramp to upper_frequency
                direction = 1 if self.get("setpoint") < upper_freq_wavelength else -1
                if abs(self.get("setpoint") - upper_freq_wavelength) > wavelength_step:
                    self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                else:
                    self._set("setpoint", upper_freq_wavelength)
                    self._set("scanning", False)

            if waveform in [3, 4]:  # triangles
                # change direction if a limit is hit
                if direction == 1 and self.get("setpoint") >= lower_freq_wavelength:
                    direction = -1
                if direction == -1 and self.get("setpoint") <= upper_freq_wavelength:
                    direction = 1

                if direction == -1:
                    if abs(self.get("setpoint") - upper_freq_wavelength) > wavelength_step:
                        self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                    else:
                        self._set("setpoint", upper_freq_wavelength)
                        direction = 1

                if direction == 1:
                    if abs(self.get("setpoint") - lower_freq_wavelength) > wavelength_step:
                        self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                    else:
                        self._set("setpoint", lower_freq_wavelength)
                        direction = -1

            if waveform in [5, 6]:  # sawtooths |/ and /| (in frequency)
                if self.get("setpoint") <= upper_freq_wavelength:
                    self._set("setpoint", lower_freq_wavelength)
                else:
                    if abs(self.get("setpoint") - upper_freq_wavelength) > wavelength_step:
                        self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                    else:
                        self._set("setpoint", upper_freq_wavelength)

            if waveform in [7, 8]:  # sawtooths |\ and \| (in frequency)
                if self.get("setpoint") >= lower_freq_wavelength:
                    self._set("setpoint", upper_freq_wavelength)
                else:
                    if abs(self.get("setpoint") - lower_freq_wavelength) > wavelength_step:
                        self._set("setpoint", self.get("setpoint") + direction * wavelength_step)
                    else:
                        self._set("setpoint", lower_freq_wavelength)

            self._integrator_ff(self.get("setpoint") - previous_setpoint)

            await asyncio.sleep(timestep)

    def stop_scan(self):
        """
        Stops any scan. The setpoint will remain at its current value. To return to the value before the scan, call
        :meth:`relock`.
        """
        self._set("scanning", False)
        logger.info("Stopping scan.")

    def ping(self) -> bool:
        """Can be used by RPC clients to prevent connection timeouts."""
        return True


def wavemeter_pi_client_args(parser):
    group = parser.add_argument_group("wavemeter server")
    group.add_argument("-s", "--server", default="::1", help="wavemeter server address")
    group.add_argument("-p", "--port", default=3281, help="wavemeter server publisher port (default: 3281)")

    group = parser.add_argument_group("lock parameters")
    group.add_argument("--setpoint", default=0., help="lock setpoint in nm")
    group.add_argument("--cp", default=0., help="proportional gain")
    group.add_argument("--ci", default=0., help="integrator gain")
    group.add_argument("--integrator_timeout", type=int, default=10000., help="don't update integrator when data"
                                                                              " is older than this value (in ms,"
                                                                              " default: 10000)")
    group.add_argument("--integrator_cutoff", default=0., help="don't update integrator when value is within this"
                                                               " distance of the setpoint")
    group.add_argument("--output_sensitivity", default=0., help="sensitivity to the output parameter in nm / <output"
                                                                " unit> (default: 0., used for feed forward on"
                                                                " setpoint changes, e.g. in scans)")
    group.add_argument("--output_offset", default=0., help="offset added to the output value (default: 0.)")
    parser.set_defaults(startup_locked=False)
    group.add_argument("--startup-locked", dest="startup_locked", action="store_true", help="enable lock immediately")

    simple_network_args(parser, [("rpc", "RPC", 3284), ("pub", "publisher (for wavelength and status updates)", 3282)])
