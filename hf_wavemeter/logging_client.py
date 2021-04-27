import time
from sipyco.pc_rpc import BestEffortClient
from sipyco.common_args import verbosity_args, init_logger_from_args

from typing import List, Any

import asyncio

from argparse import ArgumentParser

import logging
logger = logging.getLogger(__name__)


class WavemeterLoggingClient:
    """
    Logging tool for wavemeter readings. Absolute (unix epoch) and relative timestamps and a header are added
    automatically. To start a timed log of just wavemeter values (channel wavelengths and/or
    temperature/pressure), use the :meth:`log` method. To start a manual log, use :meth:`start_manual_log`.
    Logging is stopped in both cases with :meth:`stop_logging`. In either mode, entries can be added manually by calling
    :meth:`add_entry`, which also allows the addition of an externally determined quantity.

    To make use of this feature, run as an RPC target, e.g.

    ```
    from sipyco.pc_rpc import simple_server_loop
    simple_server_loop({"wl_logger": WavemeterLoggingClient()}, host, port)
    ```

    and then connect via

    ```
    wl_logger = sipyco.pc_rpc.Client(host, port, "wl_logger")
    ```

    allowing the use of

    ```
    wl_logger.add_entry(value)
    ```

    from the the code which generates the value.

    :param host: host PC of the wavemeter server
    :param port: port of the wavemeter server
    :param target: target name of the wavemeter server
    :param event_loop: asyncio event loop for the timed logging function defaults to `asyncio.get_running_loop()`
    """
    def __init__(self, host: str = "::1", port: int = 3280, target: str = "wavemeter_server", event_loop: Any = None):
        self.wm_server = BestEffortClient(host, port, target)

        self._logging = False
        self._log_interval = 1.
        self._log_file = None
        self._log_channel_list = []
        self._log_start_timestamp = -1

        self.filename = ""

        self._event_loop = event_loop if event_loop is not None else asyncio.get_event_loop()
        self._event_loop.create_task(self.keepalive_loop())

    def _get_header_string(self, timestamp: float) -> str:
        header = "#Starting log at " + time.asctime(time.localtime(timestamp)) + "\n#UTC (s)\trel. time (s)"
        for channel in self._log_channel_list:
            if type(channel) == int:
                header += "\tch{} (nm)".format(channel)
            elif channel == "T":
                header += "\ttemperature (degrees C)"
            elif channel == "p":
                header += "\tpressure (mbar)"
        return header

    def _get_wl_value_string(self) -> str:
        line = ""
        for channel in self._log_channel_list:
            if type(channel) == int:
                line += "\t{:0.8f}".format(self.wm_server.get_wavelength(channel))
            elif channel == "T":
                line += "\t{:0.2f}".format(self.wm_server.get_temperature())
            elif channel == "p":
                line += "\t{:0.1f}".format(self.wm_server.get_pressure())
        return line

    def _output_cb(self, status):
        """Used by the GUI to get the latest written line."""
        pass

    def start_logging_timed(self, channel_list: List, interval: float, filename: str, append: bool = True):
        """
        Start a coroutine which logs values at fixed intervals.

        :param channel_list: list of channels to log. Can be integers, or 'T' or 'p' for temperature or pressure
        :param interval: interval in seconds
        :param filename: output filename
        :param append: if False, overwrite existing files
        """
        if self._logging:
            logger.error("Already logging")
            return

        mode = "a" if append else "w"
        self._log_channel_list = channel_list
        try:
            self._log_file = open(filename, mode)
        except FileNotFoundError:
            logger.error("Could not open file {}".format(filename))
            return

        self.filename = filename
        self._log_start_timestamp = time.time()
        self._log_file.write(self._get_header_string(self._log_start_timestamp) + "\n")
        self._output_cb(self._get_header_string(self._log_start_timestamp))
        self._log_interval = interval
        self._logging = True
        self._event_loop.create_task(self._log_loop())
        logger.info("Started automated log, channels: {}, interval: {} s, filename: {}, append={}".format(channel_list,
                                                                                                          interval,
                                                                                                          filename,
                                                                                                          append))

    def log(self, channel_list: List, interval: float, filename: str, append: bool = True):
        """
        Log until interrupted, e.g. by a `KeyboardInterrupt`.

        :param channel_list: list of channels to log. Can be integers, or 'T' of 'p' for temperature or pressure
        :param interval: interval in seconds
        :param filename: output filename
        :param append: if False, overwrite existing files
        """
        self.start_logging_timed(channel_list, interval, filename, append)
        if self._logging:
            try:
                self._event_loop.run_forever()
            except KeyboardInterrupt:
                logger.info("Caught KeyboardInterrupt")
            finally:
                self.stop_logging()

    def stop_logging(self):
        """
        Stops manual and timed logging.
        """
        self._logging = False
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
            self.filename = ""
        logger.info("Stopped logging.")

    async def _log_loop(self):
        # store timestamp to make sure the loop exits, even if self._logging goes False and True again during the sleep
        start_timestamp = self._log_start_timestamp
        while self._logging and self._log_start_timestamp == start_timestamp:
            line = "{}\t{:0.3f}{}".format(int(time.time() + 0.5), time.time() - self._log_start_timestamp,
                                          self._get_wl_value_string())
            logger.info("New log entry:\n" + line)
            self._log_file.write(line + "\n")
            self._log_file.flush()
            self._output_cb(line)
            await asyncio.sleep(self._log_interval)

    def start_manual_log(self, channel_list: List, filename: str, additional_parameter: str = "", append: bool = True):
        """
        Starts a log file to which values can be added manually via :meth:`log_value` and which needs to be closed via
        :meth:`stop_logging`.

        :param channel_list: list of channels to log. Can be integers, or 'T' of 'p' for temperature or pressure
        :param filename: output filename
        :param additional_parameter: name of the additional parameter (if any) as it appears in the header
        :param append:  if False, overwrite existing files
        """
        if self._logging:
            logger.error("Already logging")
            return

        mode = "a" if append else "w"
        try:
            self._log_file = open(filename, mode)
        except FileNotFoundError:
            logger.error("Could not open file {}".format(filename))
            return

        self.filename = filename
        self._log_start_timestamp = time.time()
        self._log_channel_list = channel_list
        header = self._get_header_string(self._log_start_timestamp)
        if not additional_parameter == "":
            header += "\t{}".format(additional_parameter)
        self._log_file.write(header + "\n")
        self._log_file.flush()
        self._output_cb(header)
        self._logging = True
        logger.info("Started manual log, channels: {}, additional parameter: {}, filename: {}, "
                    "append={}".format(channel_list, additional_parameter, self.filename, append))

    def add_entry(self, value: Any = None):
        """
        Add point to a manual log, which needs to be started with :meth:`start_manual_log` first.

        :param value: The value to add besides the wavelengths (optional)
        """
        if not self._logging or self._log_file is None:
            logger.error("No active log file. Start one via 'start_logging_timed' or 'start_manual_log' first.")
            return
        line = "{}\t{:0.3f}{}".format(int(time.time() + 0.5), time.time() - self._log_start_timestamp,
                                      self._get_wl_value_string())
        if value is not None:
            line += "\t{}".format(value)

        logger.info("New external log entry:\n" + line)
        self._log_file.write(line + "\n")
        self._log_file.flush()
        self._output_cb(line)

    keepalive_interval = 3600.  # ping RPC server after this interval to keep the connection alive

    async def keepalive_loop(self):
        """Keep the RPC connection alive."""
        while True:
            logger.info("Pinging the RPC server to keep the connection alive")
            self.wm_server.ping()
            await asyncio.sleep(self.keepalive_interval)


GUI_available = False

try:
    from PyQt5.QtWidgets import (
        QApplication,
        QLabel,
        QLineEdit,
        QWidget,
        QPushButton,
        QCheckBox,
        QDoubleSpinBox,
        QVBoxLayout,
        QGridLayout,
        QFormLayout,
        QGroupBox)
    from asyncqt import QEventLoop

    import sys
    import os

    GUI_available = True

    class WavemeterLoggingClientGUI:
        """
        As above, but with a GUI.
        """
        def __init__(self, host: str = "::1", port: int = 3280, target: str = "wavemeter_server"):

            self._app = QApplication(sys.argv)
            self._loop = QEventLoop(self._app)
            asyncio.set_event_loop(self._loop)

            self.logging_client = WavemeterLoggingClient(host=host, port=port, target=target, event_loop=self._loop)

            self.title = "Wavelength logging client, {} at {}:{}". format(target, host, port)

            self._ui_input_elements = dict()

            self.latest_line_label = QLabel("")

            def label_cb(line):
                timestring = time.asctime(time.localtime(time.time()))
                filename = self.logging_client.filename

                self.latest_line_label.setText("At {}, added line to file {}:\n{}".format(timestring, filename, line))

            self.logging_client._output_cb = label_cb

            self._build_ui()

        def _build_ui(self):
            self.window = QWidget()
            self.window.setWindowTitle(self.title)

            self.v_layout = QVBoxLayout()

            self.window.setLayout(self.v_layout)

            options_gb = QGroupBox("Log file options")
            options_gb_layout = QFormLayout()
            options_gb.setLayout(options_gb_layout)

            self._ui_input_elements.update({"path": QLineEdit(os.getcwd())})
            options_gb_layout.addRow("Path", self._ui_input_elements["path"])
            self._ui_input_elements.update({"file": QLineEdit("wavelength_log.txt")})
            options_gb_layout.addRow("File (overwrites without asking)", self._ui_input_elements["file"])
            self._ui_input_elements.update({"append": QCheckBox()})
            self._ui_input_elements["append"].setChecked(True)
            options_gb_layout.addRow("Append", self._ui_input_elements["append"])
            channels = ""
            for chan in set(self.logging_client.wm_server.get_active_channels()):
                channels += "{},".format(chan)
            channels = channels[:-1]
            if self.logging_client.wm_server.get_temperature() != -1:
                channels += ",T"
            if self.logging_client.wm_server.get_pressure() != -1:
                channels += ",p"
            self._ui_input_elements.update({"channels": QLineEdit(channels)})
            options_gb_layout.addRow("Channels (comma separated)", self._ui_input_elements["channels"])
            self._ui_input_elements.update({"add_param": QLineEdit("")})
            options_gb_layout.addRow("Additional parameter header entry (optional)",
                                     self._ui_input_elements["add_param"])

            self.v_layout.addWidget(options_gb)

            command_layout = QGridLayout()

            auto_log_gb = QGroupBox("Automated logging")
            auto_log_gb_layout = QFormLayout()
            auto_log_gb.setLayout(auto_log_gb_layout)

            self._ui_input_elements.update({"interval": QDoubleSpinBox()})
            self._ui_input_elements["interval"].setDecimals(1)
            self._ui_input_elements["interval"].setRange(0.1, 1e5)
            self._ui_input_elements["interval"].setSingleStep(0.1)
            self._ui_input_elements["interval"].setSuffix(" s")
            self._ui_input_elements["interval"].setValue(10)
            auto_log_gb_layout.addRow("Interval", self._ui_input_elements["interval"])

            auto_log_button = QPushButton("Start automated &logging")

            def get_logging_parameters():
                channel_list = self._ui_input_elements["channels"].text().split(",")
                for i in range(len(channel_list)):
                    try:
                        channel_list[i] = int(channel_list[i])
                    except ValueError:
                        pass
                interval = self._ui_input_elements["interval"].value()
                filename = os.path.join(self._ui_input_elements["path"].text(), self._ui_input_elements["file"].text())
                append = self._ui_input_elements["append"].isChecked()

                return channel_list, interval, filename, append

            def start_auto_logging():
                channel_list, interval, filename, append = get_logging_parameters()
                logger.info("Calling start_logging_timed({}, {}, {}, {})".format(channel_list, interval, filename,
                                                                                 append))
                self.logging_client.start_logging_timed(channel_list, interval, filename, append)

            auto_log_button.clicked.connect(start_auto_logging)
            auto_log_gb_layout.addWidget(auto_log_button)
            command_layout.addWidget(auto_log_gb, 0, 0)

            start_manual_button = QPushButton("Start &manual log")

            def start_manual_log():
                channel_list, _, filename, append = get_logging_parameters()
                additional_parameter = self._ui_input_elements["add_param"].text()
                logger.info("Calling start_manual_log({}, {}, {}, {})".format(channel_list, filename,
                                                                              additional_parameter, append))
                self.logging_client.start_manual_log(channel_list, filename, additional_parameter, append)

            start_manual_button.clicked.connect(start_manual_log)
            command_layout.addWidget(start_manual_button, 0, 1)

            stop_logging_button = QPushButton("S&top logging")
            stop_logging_button.clicked.connect(self.logging_client.stop_logging)

            command_layout.addWidget(stop_logging_button, 1, 0)

            self.v_layout.addLayout(command_layout)

            add_entry_gb = QGroupBox("Manual entry")
            add_entry_gb_layout = QFormLayout()
            add_entry_gb.setLayout(add_entry_gb_layout)

            self._ui_input_elements.update({"extra_value": QLineEdit()})
            add_entry_gb_layout.addRow("Additional parameter value (optional)", self._ui_input_elements["extra_value"])

            add_log_entry_button = QPushButton("&Add log entry")

            def add_log_entry():
                value = self._ui_input_elements["extra_value"].text()
                self.logging_client.add_entry(value)

            add_log_entry_button.clicked.connect(add_log_entry)

            add_entry_gb_layout.addWidget(add_log_entry_button)

            self.v_layout.addWidget(add_entry_gb)

            self.v_layout.addWidget(self.latest_line_label)

        def run(self):
            self.window.show()
            self._loop.run_forever()
            self.logging_client.wm_server.close_rpc()


except ModuleNotFoundError as err:
    logger.warning("GUI not available due to missing modules: {}.\nRun installation.py again with "
                   "GUI_DEPENDENCIES enabled if a GUI is needed.".format(err))


def get_argparser():
    parser = ArgumentParser(description="Wavelength logging client. Specify a filename to start logging (Ch1 to Ch8 "
                                        "wavelengths by default) or leave filename blank to start GUI")
    group = parser.add_argument_group("network arguments")
    group.add_argument("-s", "--server", default="::1", help="address of the wavemeter server (default: ::1)")
    group.add_argument("-p", "--port", default=3280, help="port of the wavemeter server (default: 3280)")
    group = parser.add_argument_group("logging options")
    group.add_argument("-c", "--channel", nargs="*", default=[i for i in range(1, 9)],
                       help="list of channels to log (channel numbers, T, or p; default: 1 2 3 4 5 6 7 8)")
    group.add_argument("-i", "--interval", default=1., help="Logging interval in seconds")
    parser.set_defaults(append=False)
    group.add_argument("-a", "--append", dest="append", action="store_true")
    group.add_argument("-f", "--file", default="", help="output filename")
    parser.set_defaults(T=False, p=False)
    verbosity_args(parser)

    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)
    if args.file == "":
        if GUI_available:
            wlcg = WavemeterLoggingClientGUI(host=args.server, port=args.port)
            wlcg.run()
        else:
            logger.error("No filename specified and no GUI available - exiting")
            return
    else:
        wlc = WavemeterLoggingClient(host=args.server, port=args.port)
        channels = []
        for ch in args.channel:
            try:
                channels.append(int(ch))
            except ValueError:
                if ch in ["T", "p"]:
                    channels.append(ch)
        wlc.log(channels, float(args.interval), args.file, args.append)
        wlc.wm_server.close_rpc()


if __name__ == "__main__":
    main()
