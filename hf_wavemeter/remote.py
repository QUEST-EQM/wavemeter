import sys
from PyQt5.QtWidgets import (
 QApplication,
 QLabel,
 QWidget,
 QPushButton,
 QDoubleSpinBox,
 QCheckBox,
 QVBoxLayout,
 QHBoxLayout,
 QFormLayout,
 QGroupBox)

from PyQt5.QtGui import QFont

import time

from sipyco.sync_struct import Subscriber
from sipyco.pc_rpc import BestEffortClient
from sipyco.common_args import verbosity_args, init_logger_from_args

import asyncio
from asyncqt import QEventLoop

from argparse import ArgumentParser

import logging
logger = logging.getLogger(__name__)


class WavemeterRemote:
    """
    Simple remote control for a :class:`WavemeterServer`, providing controls to start and stop the measurement,
    calibrate, control autocalibration and displaying the timestamp of the latest successful calibration as well as
    the autocalibration countdown.

    :param host: the address at which the :class:`WavemeterServer` RPC server and Publisher are running
    :param rpc_target: name of the RPC target
    :param rpc_port: port of the RPC server
    :param notifier_port: port of the publisher (notifier "status", containing the calibration timestamp and countdown)
    :param title: the window title (generated from the RPC target name by default)
    :param window_x: initial x position of the window in pixels
    :param window_y: initial y position of the window in pixels
    :param cal_channel: startup entry for the calibration channel
    :param cal_wl: startup entry for the calibration wavelength (in nm)
    :param cal_threshold: startup entry for the autocalibration threshold (in nm)
    :param cal_interval: startup entry for the autocalibration interval (in s)
    :param cal_retry_interval: startup entry for the autocalibration retry interval (in s)
    :param start_autocal: set to start autocalibration on startup
    """
    def __init__(self, host: str = "::1", rpc_target: str = "wavemeter_server", rpc_port: int = 3280,
                 notifier_port: int = 3281, title: str = None, window_x: int = 0, window_y: int = 0,
                 cal_channel: int = 1, cal_wl: float = 633., cal_threshold: float = 0.00005,
                 cal_interval: int = 600, cal_retry_interval: int = 10, start_autocal: bool = False):

        self._app = QApplication(sys.argv)
        self._loop = QEventLoop(self._app)
        asyncio.set_event_loop(self._loop)

        self.wm_status_dict = {"autocal_countdown": 0, "calibration_timestamp": -1}

        self._subscriber = Subscriber("status", self._subscriber_init_cb, self._subscriber_mod_cb)
        logger.info("Connecting to publisher at {}:{}, notifier name: status".format(host, notifier_port))
        self._loop.run_until_complete(self._subscriber.connect(host, notifier_port))

        logger.info("Connecting to RPC server at {}:{}, target name: {}".format(host, rpc_port, rpc_target))
        self._rpc_client = BestEffortClient(host, rpc_port, rpc_target)
        self._loop.create_task(self.keepalive_loop())

        self.title = title if title is not None else "Wavemeter remote control ({} at {})".format(rpc_target, host)

        self._ui_input_elements = dict()  # stores all input elements, used on function calls to retrieve their values
        self._build_ui(window_x, window_y, cal_channel, cal_wl, cal_threshold, cal_interval, cal_retry_interval)

        if start_autocal and self._rpc_client is not None:
            self._rpc_client.start_autocalibration(cal_channel, cal_wl, cal_threshold, cal_interval, cal_retry_interval)

    def _build_ui(self, window_x, window_y, cal_channel, cal_wl, cal_threshold, cal_interval, cal_retry_interval):
        self.window = QWidget()
        self.window.setWindowTitle(self.title)

        self.window.move(window_x, window_y)

        self.v_layout = QVBoxLayout()

        self.window.setLayout(self.v_layout)

        indicator_font = QFont()
        indicator_font.setPointSize(18)
        self.calibration_timestamp_display = QLabel()
        self.calibration_timestamp_display.setFont(indicator_font)
        self.calibration_countdown_display = QLabel()
        self.calibration_countdown_display.setFont(indicator_font)

        startstop_gb = QGroupBox("Measurement")
        startstop_gb_layout = QHBoxLayout()
        startstop_gb.setLayout(startstop_gb_layout)

        start_button = QPushButton("&Start measurement")

        def start():
            if self._rpc_client is not None:
                logger.info("sending RPC start_measurement()")
                self._rpc_client.start_measurement()
        start_button.clicked.connect(start)
        startstop_gb_layout.addWidget(start_button)

        stop_button = QPushButton("S&top measurement")

        def stop():
            if self._rpc_client is not None:
                logger.info("sending RPC stop_measurement()")
                self._rpc_client.stop_measurement()
        stop_button.clicked.connect(stop)
        startstop_gb_layout.addWidget(stop_button)

        self.v_layout.addWidget(startstop_gb)

        cal_gb = QGroupBox("Calibration")
        cal_gb_outer_layout = QVBoxLayout()
        cal_gb_outer_layout.addWidget(self.calibration_timestamp_display)
        cal_gb_outer_layout.addWidget(self.calibration_countdown_display)
        cal_gb_layout = QHBoxLayout()
        cal_gb_outer_layout.addLayout(cal_gb_layout)
        cal_gb.setLayout(cal_gb_outer_layout)

        calibrate_gb = QGroupBox("Calibrate")
        calibrate_gb_layout = QFormLayout()
        calibrate_gb.setLayout(calibrate_gb_layout)

        calibrate_button = QPushButton("&Calibrate")

        def calibrate():
            ch = int(self._ui_input_elements["cal_channel"].value())
            wl = self._ui_input_elements["cal_wl"].value()
            if self._rpc_client is not None:
                logger.info("sending RPC calibrate(channel={}, wavelength={})".format(ch, wl))
                self._rpc_client.calibrate(ch, wl)

        calibrate_button.clicked.connect(calibrate)

        calibrate_gb_layout.addRow("", calibrate_button)

        self._ui_input_elements.update({"cal_channel": QDoubleSpinBox()})
        self._ui_input_elements["cal_channel"].setRange(1, 100)
        self._ui_input_elements["cal_channel"].setSingleStep(1)
        self._ui_input_elements["cal_channel"].setDecimals(0)
        self._ui_input_elements["cal_channel"].setValue(cal_channel)
        calibrate_gb_layout.addRow("Channel", self._ui_input_elements["cal_channel"])

        self._ui_input_elements.update({"cal_wl": QDoubleSpinBox()})
        self._ui_input_elements["cal_wl"].setRange(1., 10000.)
        self._ui_input_elements["cal_wl"].setSingleStep(1e-10)
        self._ui_input_elements["cal_wl"].setDecimals(10)
        self._ui_input_elements["cal_wl"].setSuffix(" nm")
        self._ui_input_elements["cal_wl"].setValue(cal_wl)
        calibrate_gb_layout.addRow("Wavelength", self._ui_input_elements["cal_wl"])

        cal_gb_layout.addWidget(calibrate_gb)

        autocalibration_gb = QGroupBox("Autocalibration")
        autocalibration_gb_layout = QFormLayout()
        autocalibration_gb.setLayout(autocalibration_gb_layout)

        start_autocalibration_button = QPushButton("Start &autocalibration")

        def start_autocalibration():
            ch = int(self._ui_input_elements["autocal_channel"].value())
            wl = self._ui_input_elements["autocal_wl"].value()
            thr = self._ui_input_elements["autocal_threshold"].value()
            interval = int(self._ui_input_elements["autocal_interval"].value())
            retry_interval = int(self._ui_input_elements["autocal_retry_interval"].value())
            if self._rpc_client is not None:
                logger.info("sending RPC start_autocalibration(channel={}, wavelength={}, threshold={}, "
                            "interval={}, retry_interval={})".format(ch, wl, thr, interval, retry_interval))
                self._rpc_client.start_autocalibration(ch, wl, thr, interval, retry_interval)

        start_autocalibration_button.clicked.connect(start_autocalibration)

        autocalibration_gb_layout.addRow("", start_autocalibration_button)

        self._ui_input_elements.update({"autocal_channel": QDoubleSpinBox()})
        self._ui_input_elements["autocal_channel"].setRange(1, 100)
        self._ui_input_elements["autocal_channel"].setSingleStep(1)
        self._ui_input_elements["autocal_channel"].setDecimals(0)
        self._ui_input_elements["autocal_channel"].setValue(cal_channel)
        autocalibration_gb_layout.addRow("Channel", self._ui_input_elements["autocal_channel"])

        self._ui_input_elements.update({"autocal_wl": QDoubleSpinBox()})
        self._ui_input_elements["autocal_wl"].setRange(1., 10000.)
        self._ui_input_elements["autocal_wl"].setSingleStep(1e-10)
        self._ui_input_elements["autocal_wl"].setDecimals(10)
        self._ui_input_elements["autocal_wl"].setSuffix(" nm")
        self._ui_input_elements["autocal_wl"].setValue(cal_wl)
        autocalibration_gb_layout.addRow("Wavelength", self._ui_input_elements["autocal_wl"])

        self._ui_input_elements.update({"autocal_threshold": QDoubleSpinBox()})
        self._ui_input_elements["autocal_threshold"].setRange(1e-10, 10000.)
        self._ui_input_elements["autocal_threshold"].setSingleStep(1e-10)
        self._ui_input_elements["autocal_threshold"].setDecimals(10)
        self._ui_input_elements["autocal_threshold"].setSuffix(" nm")
        self._ui_input_elements["autocal_threshold"].setValue(cal_threshold)
        autocalibration_gb_layout.addRow("Threshold", self._ui_input_elements["autocal_threshold"])

        self._ui_input_elements.update({"autocal_interval": QDoubleSpinBox()})
        self._ui_input_elements["autocal_interval"].setRange(1, 100000)
        self._ui_input_elements["autocal_interval"].setSingleStep(1)
        self._ui_input_elements["autocal_interval"].setDecimals(0)
        self._ui_input_elements["autocal_interval"].setSuffix(" s")
        self._ui_input_elements["autocal_interval"].setValue(cal_interval)
        autocalibration_gb_layout.addRow("Interval", self._ui_input_elements["autocal_interval"])

        self._ui_input_elements.update({"autocal_retry_interval": QDoubleSpinBox()})
        self._ui_input_elements["autocal_retry_interval"].setRange(1, 100000)
        self._ui_input_elements["autocal_retry_interval"].setSingleStep(1)
        self._ui_input_elements["autocal_retry_interval"].setDecimals(0)
        self._ui_input_elements["autocal_retry_interval"].setSuffix(" s")
        self._ui_input_elements["autocal_retry_interval"].setValue(cal_retry_interval)
        autocalibration_gb_layout.addRow("Retry interval", self._ui_input_elements["autocal_retry_interval"])

        stop_autocalibration_button = QPushButton("St&op autocalibration")

        def stop_autocalibration():
            if self._rpc_client is not None:
                logger.info("sending RPC stop_autocalibration()")
                self._rpc_client.stop_autocalibration()

        stop_autocalibration_button.clicked.connect(stop_autocalibration)

        autocalibration_gb_layout.addRow("", stop_autocalibration_button)

        cal_gb_layout.addWidget(autocalibration_gb)

        self.v_layout.addWidget(cal_gb)

        exposure_gb = QGroupBox("Exposure")
        exposure_gb_layout = QHBoxLayout()
        exposure_gb.setLayout(exposure_gb_layout)

        control_form = QFormLayout()
        self._ui_input_elements.update({"exp_channel": QDoubleSpinBox()})
        self._ui_input_elements["exp_channel"].setRange(1, 100)
        self._ui_input_elements["exp_channel"].setSingleStep(1)
        self._ui_input_elements["exp_channel"].setDecimals(0)
        self._ui_input_elements["exp_channel"].setValue(1)
        control_form.addRow("Channel", self._ui_input_elements["exp_channel"])

        self._ui_input_elements.update({"exp_time1": QDoubleSpinBox()})
        self._ui_input_elements["exp_time1"].setRange(1, 2000)
        self._ui_input_elements["exp_time1"].setSingleStep(1)
        self._ui_input_elements["exp_time1"].setDecimals(0)
        self._ui_input_elements["exp_time1"].setSuffix(" ms")
        self._ui_input_elements["exp_time1"].setValue(1)
        control_form.addRow("Time 1", self._ui_input_elements["exp_time1"])

        self._ui_input_elements.update({"exp_time2": QDoubleSpinBox()})
        self._ui_input_elements["exp_time2"].setRange(0, 2000)
        self._ui_input_elements["exp_time2"].setSingleStep(1)
        self._ui_input_elements["exp_time2"].setDecimals(0)
        self._ui_input_elements["exp_time2"].setSuffix(" ms")
        self._ui_input_elements["exp_time2"].setValue(1)
        control_form.addRow("Time 2", self._ui_input_elements["exp_time2"])

        self._ui_input_elements.update({"exp_auto": QCheckBox()})
        control_form.addRow("Auto adjust", self._ui_input_elements["exp_auto"])

        exposure_gb_layout.addLayout(control_form)

        exposure_button_layout = QVBoxLayout()

        exposure_get_button = QPushButton("&Get")

        def exposure_get():
            channel = int(self._ui_input_elements["exp_channel"].value())
            if self._rpc_client is not None:
                logger.info("sending RPC get_exposure_time({}, 1)".format(channel))
                time1 = self._rpc_client.get_exposure_time(channel, 1)
                logger.info("sending RPC get_exposure_time({}, 2)".format(channel))
                time2 = self._rpc_client.get_exposure_time(channel, 2)
                logger.info("sending RPC get_exposure_auto_adjust({})".format(channel))
                auto = self._rpc_client.get_exposure_auto_adjust(channel)
                self._ui_input_elements["exp_time1"].setValue(time1)
                self._ui_input_elements["exp_time2"].setValue(time2)
                self._ui_input_elements["exp_auto"].setChecked(auto)

        exposure_get_button.clicked.connect(exposure_get)

        exposure_button_layout.addWidget(exposure_get_button)

        exposure_set_button = QPushButton("S&et")

        def exposure_set():
            channel = int(self._ui_input_elements["exp_channel"].value())
            time1 = int(self._ui_input_elements["exp_time1"].value())
            time2 = int(self._ui_input_elements["exp_time2"].value())
            auto = bool(self._ui_input_elements["exp_auto"].isChecked())
            if self._rpc_client is not None:
                logger.info("sending RPC set_exposure_time({}, 1, {})".format(channel, time1))
                self._rpc_client.set_exposure_time(channel, 1, time1)
                logger.info("sending RPC set_exposure_time({}, 2, {})".format(channel, time2))
                self._rpc_client.set_exposure_time(channel, 2, time2)
                logger.info("sending RPC set_exposure_auto_adjust({}, {})".format(channel, auto))
                self._rpc_client.set_exposure_auto_adjust(channel, auto)

        exposure_set_button.clicked.connect(exposure_set)

        exposure_button_layout.addWidget(exposure_set_button)

        exposure_gb_layout.addLayout(exposure_button_layout)

        self.v_layout.addWidget(exposure_gb)

    def _update_cal_timestamp(self):
        self.calibration_timestamp_display.setText(
            "Last successful"
            " calibration:\n{}".format(time.asctime(time.localtime(self.wm_status_dict["calibration_timestamp"]))))

    def _update_cal_countdown(self):
        self.calibration_countdown_display.setText(
            "Next autocalibration attempt in {:.0f} s".format(self.wm_status_dict["autocal_countdown"]))

    def _subscriber_init_cb(self, data):
        self.wm_status_dict = data
        self._update_cal_timestamp()
        self._update_cal_countdown()
        return data

    def _subscriber_mod_cb(self, mod):
        try:
            if mod["key"] == "calibration_timestamp":
                self._update_cal_timestamp()
            elif mod["key"] == "autocal_countdown":
                self._update_cal_countdown()
        except KeyError:
            pass

    keepalive_interval = 3600.  # ping RPC server after this interval to keep the connection alive

    async def keepalive_loop(self):
        """Keep the RPC connection alive"""
        while True:
            logger.info("Pinging the RPC server to keep the connection alive")
            self._rpc_client.ping()
            await asyncio.sleep(self.keepalive_interval)

    def run(self):
        self.window.show()
        self._loop.run_forever()
        if self._subscriber is not None:
            self._loop.run_until_complete(self._subscriber.close())
        if self._rpc_client is not None:
            self._rpc_client.close_rpc()


def get_argparser():
    parser = ArgumentParser(description="HighFinesse wavemeter remote control")
    group = parser.add_argument_group("network arguments")
    group.add_argument("-s", "--server", default="::1", help="address of the host running the wavemeter server"
                                                             "(default: ::1 (localhost))")
    group.add_argument("--rpc_port", type=int, default=3280, help="wavemeter server RPC port (default: 3280)")
    group.add_argument("--rpc_target", default="wavemeter_server", help="name of the RPC target "
                                                                        "(default: 'wavemeter_server')")
    group.add_argument("--pub_port", type=int, default=3281, help="wavemeter status publisher port (default: 3281)")

    group.add_argument_group("window options")
    group.add_argument("--title", default=None, help="window title")
    group.add_argument("-x", type=int, default=0, help="window x position in pixels (default: 0)")
    group.add_argument("-y", type=int, default=0, help="window y position in pixels (default: 0)")

    group = parser.add_argument_group("calibration options")
    parser.set_defaults(start_autocal=False)
    group.add_argument("-a", "--autocal", dest="start_autocal", action="store_true",
                       help="enable autocalibration at startup")
    group.add_argument("-c", "--cal_channel", type=int, default=1, help="calibration channel")
    group.add_argument("-w", "--cal_wl", type=float, default=633., help="calibration wavelength in nm")
    group.add_argument("-t", "--cal_threshold", type=float, default=0.00005, help="autocalibration threshold in nm - "
                                                                                  "calibration is only performed if "
                                                                                  "the current reading si within this "
                                                                                  "range of the nominal wavelength")
    group.add_argument("-i", "--cal_interval", type=int, default=600., help="autocalibration interval in s")
    group.add_argument("-r", "--cal_retry_interval", type=int, default=10., help="autocalibration retry interval in s")

    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)

    wlr = WavemeterRemote(host=args.server, rpc_target=args.rpc_target, rpc_port=args.rpc_port,
                          notifier_port=args.pub_port, title=args.title, window_x=args.x, window_y=args.y,
                          cal_channel=args.cal_channel, cal_wl=args.cal_wl, cal_threshold=args.cal_threshold,
                          cal_interval=args.cal_interval, cal_retry_interval=args.cal_retry_interval,
                          start_autocal=args.start_autocal)
    wlr.run()


if __name__ == "__main__":
    main()
