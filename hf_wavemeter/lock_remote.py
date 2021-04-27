import sys
from PyQt5.QtWidgets import (
 QApplication,
 QLineEdit,
 QLCDNumber,
 QWidget,
 QPushButton,
 QCheckBox,
 QComboBox,
 QDoubleSpinBox,
 QVBoxLayout,
 QHBoxLayout,
 QGridLayout,
 QSplitter,
 QFormLayout,
 QGroupBox)
from PyQt5.QtCore import Qt

from sipyco.sync_struct import Subscriber
from sipyco.pc_rpc import BestEffortClient
from sipyco.common_args import verbosity_args, init_logger_from_args

import asyncio
from asyncqt import QEventLoop

from numpy import nan

from hf_wavemeter.gui.stripchart import Stripchart2Traces
from hf_wavemeter.gui.interferogram_widget import InterferogramWidget
import pyqtgraph

from typing import Any

from argparse import ArgumentParser

import logging
logger = logging.getLogger(__name__)


class WavemeterLockRemote:
    """
    Connects to a :class:`WavemeterPIClient` to monitor and control a laser lock. The GUI consists of up to seven
    elements:

    - a stripchart displaying the current value (wavelength or frequency) and setpoint
    - a scalable number display (wavelength or frequency)
    - interferograms with optional exposure controls
    - a panel with lock controls
    - a panel with scan controls
    - an info panel showing current values for the locking parameters, integrator and output values
    - controls for the (optional) auxiliary output

    :param host: the address at which the :class:`WavemeterPIClient` RPC server and Publisher are running
    :param rpc_target: name of the RPC target
    :param rpc_port: port of the RPC server
    :param notifier_name: name of the notifier
    :param notifier_port: port of the publisher
    :param wavemeter_host: host of the wavemeter server (only needed for interferograms, default = same as host)
    :param wavemeter_rpc_port: rpc port of the wavemeter server (only needed for interferograms)
    :param wavemeter_rpc_target: target name for wavemeter server rpcs (only needed for interferograms)
    :param title: the window title (generated from the server details by default)
    :param window_width: initial width of the window in pixels
    :param window_x: initial x position of the window in pixels
    :param window_y: initial y position of the window in pixels
    :param enable_stripchart: show the stripchart (plot of actual value and setpoint)
    :param stripchart_height: initial stripchart height in pixels
    :param stripchart_num_points: number of data points displayed in the stripchart
    :param plot_freq: True for frequency plot, False for wavelength plot
    :param plot_offset: y offset of the plot (unit is MHz for frequencies and nm for wavelengths)
    :param plot_pen_actual: pen specification for the actual value trace (see pyqtgraph documentation)
    :param plot_symbol_actual: symbol specification for the actual value trace
    :param plot_pen_setpoint: pen specification for the setpoint trace
    :param plot_symbol_setpoint: symbol specification for the setpoint trace
    :param plot_bgr: bgr color of the plot
    :param plot_bgr_alarm: bgr color of the plot when the output is (close to) railing
                           (as determined by the lock client)
    :param enable_lcd: show the current value (wavelength or frequency) as a scalable number
    :param lcd_height: lcd height in pixels
    :param lcd_ndigits: number of digits in the lcd
    :param lcd_freq: start with the number display showing the frequency (default is wavelength)
    :param enable_lock_ctrl: show lock controls
    :param enable_scan_ctrl: show scan controls
    :param enable_extra_info: show info panel
    :param enable_interferograms: display the interferograms
    :param interferogram_update_interval: number of values after which to update the interferograms
    :param show_interferogram_update_control: show the spinbox to adjust interferogram update rate
    :param if0_pen: pen specification for the 1st interferogram
    :param if1_pen: pen specification for the 2nd interferogram
    :param interferogram_bgr: bgr color of the interferogram plot
    :param interferogram_height: initial height of the interferogram plot in pixels
    :param interferogram_exposure_control: enable control of wavemeter exposure times
    """
    def __init__(self, host: str = "::1", rpc_target: str = "",  rpc_port: int = 3284, notifier_name: str = "",
                 notifier_port: int = 3282, wavemeter_host: str = "::1", wavemeter_rpc_port: int = 3280,
                 wavemeter_rpc_target: str = "wavemeter_server", title: str = None, window_width: int = 611,
                 window_x: int = 0, window_y: int = 0, enable_stripchart: bool = True, stripchart_height: int = 350,
                 stripchart_num_points: int = 100, plot_freq: bool = True, plot_offset: float = 0.,
                 plot_pen_actual: Any = pyqtgraph.mkPen(color="k", width=3), plot_symbol_actual: Any = None,
                 plot_pen_setpoint: Any = pyqtgraph.mkPen(color="g", width=2), plot_symbol_setpoint: Any = None,
                 plot_bgr: str = "w", plot_bgr_alarm: str = "r", enable_lcd: bool = True,
                 lcd_height: int = 200, lcd_ndigits: int = 10, lcd_freq: bool = False, enable_lock_ctrl: bool = True,
                 enable_scan_ctrl: bool = True, enable_extra_info: bool = True, enable_aux_ctrl: bool = False,
                 enable_interferograms: bool = False, interferogram_update_interval: int = 1,
                 show_interferogram_update_control: bool = False, if0_pen: Any = pyqtgraph.mkPen(color="g", width=1),
                 if1_pen: Any = pyqtgraph.mkPen(color="b", width=1), interferogram_bgr: str = "w",
                 interferogram_height: int = 150, interferogram_exposure_control: bool = False):

        # user interface components
        self._enable_stripchart = enable_stripchart
        self._enable_lcd = enable_lcd
        self._enable_lock_ctrl = enable_lock_ctrl
        self._enable_scan_ctrl = enable_scan_ctrl
        self._enable_extra_info = enable_extra_info
        self._enable_aux_ctrl = enable_aux_ctrl
        self._enable_interferograms = enable_interferograms

        self.lcd_ndigits = lcd_ndigits

        self.stripchart_pen1 = plot_pen_actual
        self.stripchart_symbol1 = plot_symbol_actual
        self.stripchart_pen2 = plot_pen_setpoint
        self.stripchart_symbol2 = plot_symbol_setpoint
        self.stripchart_bgr = plot_bgr
        self.stripchart_bgr_alarm = plot_bgr_alarm

        self.if0_pen = if0_pen
        self.if1_pen = if1_pen
        self.interferogram_bgr = interferogram_bgr
        self.interferogram_update_interval = interferogram_update_interval
        self.interferogram_value_counter = interferogram_update_interval + 1

        self._app = QApplication(sys.argv)
        self._loop = QEventLoop(self._app)
        asyncio.set_event_loop(self._loop)

        self.status_dict = {
            "latest_value": 0.,
            "latest_timestamp": -1,
            "locked": False,
            "setpoint": 0.,
            "setpoint_noscan": 0.,
            "cp": 0.,
            "ci": 0.,
            "integrator": 0.,
            "output": 0.,
            "output_offset": 0.,
            "output_rail_warning": False,
            "scanning": False,
            "output_sensitivity": 0.
        }

        #  subscribe to lock client value updates if needed
        if self._enable_stripchart or self._enable_lcd or self._enable_extra_info:
            self._subscriber = Subscriber(notifier_name, self._subscriber_init_cb, self._subscriber_mod_cb)
            logger.info("Connecting to publisher at {}:{}, notifier name: {}".format(host, notifier_port,
                                                                                     notifier_name))
            self._loop.run_until_complete(self._subscriber.connect(host, notifier_port))
        else:
            logger.info("Not connecting to publisher since no GUI elements require it")
            self._subscriber = None

        self.output_unit = ""
        self.aux_output_unit = ""
        #  connect client for RPCs if needed
        #  (note: interferograms use their own RPC client [to the wavemeter server], but they still need this one
        #  [to the lock client] to obtain the channel id)
        if self._enable_lock_ctrl or self._enable_scan_ctrl or self._enable_interferograms:
            logger.info("Connecting to RPC server at {}:{}, target name: {}".format(host, rpc_port, rpc_target))
            self._rpc_client = BestEffortClient(host, rpc_port, rpc_target)
            self._loop.create_task(self.keepalive_loop())
            self.output_unit = self._rpc_client.get_output_unit()
            self.aux_output_unit = self._rpc_client.get_aux_output_unit()
            try:
                self.channel_id = int(self._rpc_client.get_channel()[2:])
            except ValueError:  # not a wavelength channel
                self._enable_interferograms = False
        else:
            logger.info("Not connecting to RPC server since no GUI elements require it")
            self._rpc_client = None

        self.plot_freq = plot_freq
        self.plot_offset = plot_offset

        self.lcd_freq = lcd_freq

        self.title = title if title is not None else "Lock remote control ({} at {}:{}, " \
                                                     "{} at {}:{})".format(rpc_target, host, rpc_port, notifier_name,
                                                                           host, notifier_port)

        if self._enable_interferograms:
            self.interferograms = InterferogramWidget(rpc_host=wavemeter_host, rpc_port=wavemeter_rpc_port,
                                                      rpc_target=wavemeter_rpc_target,
                                                      channel_id=self.channel_id, if0_pen=if0_pen, if1_pen=if1_pen,
                                                      bgr_color=interferogram_bgr,
                                                      update_interval=interferogram_update_interval,
                                                      show_interval_spinbox=show_interferogram_update_control,
                                                      show_exposure_ctrl=interferogram_exposure_control)

        self._ui_input_elements = dict()  # stores all input elements, used on function calls to retrieve their values
        self._build_ui(window_width, window_x, window_y, stripchart_height, stripchart_num_points,
                       lcd_height, interferogram_height)

    def _build_ui(self, window_width, window_x, window_y, stripchart_height, stripchart_num_points,
                  lcd_height, interferogram_height):
        self.window = QWidget()
        self.window.setWindowTitle(self.title)
        lock_ctrl_height = 100
        scan_ctrl_height = 220
        extra_info_height = 100
        aux_ctrl_height = 50
        window_height = stripchart_height * self._enable_stripchart\
            + lcd_height * self._enable_lcd\
            + interferogram_height * self._enable_interferograms\
            + lock_ctrl_height * self._enable_lock_ctrl\
            + scan_ctrl_height * self._enable_scan_ctrl\
            + extra_info_height * self._enable_extra_info\
            + aux_ctrl_height * self._enable_aux_ctrl
        self.window.resize(window_width, window_height)
        self.window.move(window_x, window_y)

        # The layout only contains one element (the splitter), but it is needed to rescale widgets as window is rescaled
        self.v_layout = QVBoxLayout()
        self.v_splitter = QSplitter(Qt.Vertical)
        self.v_layout.addWidget(self.v_splitter)

        self.window.setLayout(self.v_layout)
        self.v_layout.addWidget(self.v_splitter)

        # various (greyed out) indicator elements
        self.lock_indicator = QCheckBox("Lock enabled")
        self.lock_indicator.setEnabled(False)
        self.scanning_indicator = QCheckBox("Scanning")
        self.scanning_indicator.setEnabled(False)
        self.setpoint_display = QLineEdit()
        self.setpoint_display.setReadOnly(True)
        self.setpoint_noscan_display = QLineEdit()
        self.setpoint_noscan_display.setReadOnly(True)
        self.cp_display = QLineEdit()
        self.cp_display.setReadOnly(True)
        self.ci_display = QLineEdit()
        self.ci_display.setReadOnly(True)
        self.output_offset_display = QLineEdit()
        self.output_offset_display.setReadOnly(True)
        self.integrator_display = QLineEdit()
        self.integrator_display.setReadOnly(True)
        self.output_display = QLineEdit()
        self.output_display.setReadOnly(True)
        self.output_sensitivity_display = QLineEdit()
        self.output_sensitivity_display.setReadOnly(True)
        self.aux_output_display = QLineEdit()
        self.aux_output_display.setReadOnly(True)

        splitter_sizes = []
        if self._enable_stripchart:
            self._build_stripchart(stripchart_num_points)
            splitter_sizes.append(stripchart_height)
        if self._enable_lcd:
            self._build_lcd()
            splitter_sizes.append(lcd_height)
        if self._enable_interferograms:
            self.v_splitter.addWidget(self.interferograms)
            splitter_sizes.append(interferogram_height)
        self._loop.run_until_complete(asyncio.sleep(0.1))  # give subscriber a chance to get default values for UI
        if self._enable_lock_ctrl:
            self._build_lock_ctrl()
            splitter_sizes.append(lock_ctrl_height)
        if self._enable_scan_ctrl:
            self._build_scan_ctrl()
            splitter_sizes.append(scan_ctrl_height)
        if self._enable_extra_info:
            self._build_extra_info()
            splitter_sizes.append(extra_info_height)
        if self._enable_aux_ctrl:
            self._build_aux_ctrl()
            splitter_sizes.append(aux_ctrl_height)
        self.v_splitter.setSizes(splitter_sizes)

    def _build_stripchart(self, num_points):
        plot_title = ""
        if self.plot_freq:
            plot_y_label = "frequency (MHz)"
            if self.plot_offset != 0:
                plot_title = "offset: {:.11} THz".format(self.plot_offset * 1e-6)
        else:
            plot_y_label = "vac. wavelength (nm)"
            if self.plot_offset != 0:
                plot_title = "offset: {:.10} nm".format(self.plot_offset)

        self.stripchart = Stripchart2Traces(num_points=num_points, xvalues=True, offset=self.plot_offset,
                                            plot_title=plot_title, plot_pen=self.stripchart_pen1,
                                            plot_symbol=self.stripchart_symbol1, plot_pen2=self.stripchart_pen2,
                                            plot_symbol2=self.stripchart_symbol2, plot_bgr=self.stripchart_bgr,
                                            plot_bgr_alarm=self.stripchart_bgr_alarm,
                                            x_label="wavemeter time stamp (s)", y_label=plot_y_label, y_grid=True)
        clear_button = QPushButton("&Clear")
        clear_button.clicked.connect(self.stripchart.clear_data)

        layout = QVBoxLayout()
        layout.addWidget(self.stripchart)
        layout.addWidget(clear_button)
        widget = QWidget()
        widget.setLayout(layout)
        self.v_splitter.addWidget(widget)

    def _add_stripchart_point(self):
        if self.status_dict["latest_value"] < 0:  # ignore error codes
            return
        if self.plot_freq:  # plot frequency in MHz
            self.stripchart.add_point(self.status_dict["latest_timestamp"] * 1e-3,
                                      299792456e3 / self.status_dict["latest_value"],
                                      299792456e3 / self.status_dict["setpoint"] if self.status_dict["locked"] else nan)
        else:  # plot wavelength in nm
            self.stripchart.add_point(self.status_dict["latest_timestamp"] * 1e-3,
                                      self.status_dict["latest_value"],
                                      self.status_dict["setpoint"] if self.status_dict["locked"] else nan)

    def _build_lcd(self):
        layout = QVBoxLayout()
        self.lcd = QLCDNumber()
        self.lcd.setSmallDecimalPoint(True)
        self.lcd.setDigitCount(self.lcd_ndigits)
        freq_checkbox = QCheckBox("Display frequency")
        freq_checkbox.setChecked(self.lcd_freq)

        def switch_wl_freq():
            self.lcd_freq = freq_checkbox.isChecked()
            self._update_lcd()
        freq_checkbox.clicked.connect(switch_wl_freq)
        layout.addWidget(self.lcd)
        layout.addWidget(freq_checkbox)
        widget = QWidget()
        widget.setLayout(layout)
        self.v_splitter.addWidget(widget)

    def _update_lcd(self):
        if self.status_dict["latest_value"] > 0:
            value = 299792456e-3 / self.status_dict["latest_value"] if self.lcd_freq \
                else self.status_dict["latest_value"]
            # next line is to keep number of decimals constant (i.e. display trailing zeros)
            self.lcd.display("{{:.0{}f}}".format(self.lcd_ndigits-len(str(int(value)))).format(value))
        elif self.status_dict["latest_value"] == -2:  # bad signal
            self.lcd.display("b")
        elif self.status_dict["latest_value"] == -3:  # underexposed
            self.lcd.display("u")
        elif self.status_dict["latest_value"] == -4:  # overexposed
            self.lcd.display("o")
        else:
            self.lcd.display(self.status_dict["latest_value"])

    def _build_lock_ctrl(self):
        layout = QHBoxLayout()

        lock_gb = QGroupBox("")
        lock_gb_layout = QFormLayout()

        lock_button = QPushButton("&Lock")

        def lock():
            setpoint = self._ui_input_elements["lock_setpoint"].value()
            cp = self._ui_input_elements["lock_cp"].value()
            ci = self._ui_input_elements["lock_ci"].value()
            if self._rpc_client is not None:
                logger.info("sending RPC lock(setpoint={}, cp={}, ci={})".format(setpoint, cp, ci))
                self._rpc_client.lock(setpoint, cp, ci)
        lock_button.clicked.connect(lock)
        lock_gb_layout.addWidget(lock_button)

        self._ui_input_elements.update({"lock_setpoint": QDoubleSpinBox()})
        self._ui_input_elements["lock_setpoint"].setDecimals(10)
        self._ui_input_elements["lock_setpoint"].setRange(0., 1e4)
        self._ui_input_elements["lock_setpoint"].setSingleStep(1e-5)
        self._ui_input_elements["lock_setpoint"].setSuffix(" nm")
        self._ui_input_elements["lock_setpoint"].setValue(self.status_dict["setpoint_noscan"])
        lock_gb_layout.addRow("Setpoint", self._ui_input_elements["lock_setpoint"])

        self._ui_input_elements.update({"lock_cp": QDoubleSpinBox()})
        self._ui_input_elements["lock_cp"].setDecimals(2)
        self._ui_input_elements["lock_cp"].setRange(-1e4, 1e4)
        self._ui_input_elements["lock_cp"].setSingleStep(0.01)
        self._ui_input_elements["lock_cp"].setSuffix(" {}/nm".format(self.output_unit))
        self._ui_input_elements["lock_cp"].setValue(self.status_dict["cp"])
        lock_gb_layout.addRow("cp", self._ui_input_elements["lock_cp"])

        self._ui_input_elements.update({"lock_ci": QDoubleSpinBox()})
        self._ui_input_elements["lock_ci"].setDecimals(2)
        self._ui_input_elements["lock_ci"].setRange(-1e4, 1e4)
        self._ui_input_elements["lock_ci"].setSingleStep(0.01)
        self._ui_input_elements["lock_ci"].setSuffix(" {}/(nm*ms)".format(self.output_unit))
        self._ui_input_elements["lock_ci"].setValue(self.status_dict["ci"])
        lock_gb_layout.addRow("ci", self._ui_input_elements["lock_ci"])

        lock_gb.setLayout(lock_gb_layout)
        layout.addWidget(lock_gb)

        column2_gb = QGroupBox("")
        column2 = QVBoxLayout()

        column2.addWidget(self.lock_indicator)
        self.lock_indicator.setCheckState(self.status_dict["locked"])

        unlock_button = QPushButton("&Unlock")

        def unlock():
            if self._rpc_client is not None:
                logger.info("sending RPC unlock()")
                self._rpc_client.unlock()
        unlock_button.clicked.connect(unlock)
        column2.addWidget(unlock_button)

        relock_button = QPushButton("R&elock")

        def relock():
            if self._rpc_client is not None:
                logger.info("sending RPC relock()")
                self._rpc_client.relock()
        relock_button.clicked.connect(relock)
        column2.addWidget(relock_button)

        reset_integrator_button = QPushButton("&Reset integrator")

        def reset_integrator():
            if self._rpc_client is not None:
                logger.info("sending RPC reset_integrator()")
                self._rpc_client.reset_integrator()
        reset_integrator_button.clicked.connect(reset_integrator)
        column2.addWidget(reset_integrator_button)

        column2_gb.setLayout(column2)
        layout.addWidget(column2_gb)

        column3_gb = QGroupBox()
        column3 = QGridLayout()

        change_setpoint_button = QPushButton("C&hange Setpoint")

        def change_setpoint():
            if self._rpc_client is not None:
                logger.info("sending RPC change_setpoint({})"
                            .format(self._ui_input_elements["change_setpoint"].value()))
                self._rpc_client.change_lock_setpoint(self._ui_input_elements["change_setpoint"].value())
        change_setpoint_button.clicked.connect(change_setpoint)
        column3.addWidget(change_setpoint_button, 0, 0, Qt.AlignLeft)
        self._ui_input_elements.update({"change_setpoint": QDoubleSpinBox()})
        self._ui_input_elements["change_setpoint"].setDecimals(10)
        self._ui_input_elements["change_setpoint"].setRange(0., 1e4)
        self._ui_input_elements["change_setpoint"].setSingleStep(1e-5)
        self._ui_input_elements["change_setpoint"].setSuffix(" nm")
        self._ui_input_elements["change_setpoint"].setValue(self.status_dict["setpoint_noscan"])
        column3.addWidget(self._ui_input_elements["change_setpoint"], 0, 1, Qt.AlignLeft)

        setpoint_step_button = QPushButton("Setpoint s&tep")

        def setpoint_step():
            if self._rpc_client is not None:
                logger.info("sending RPC setpoint_step_mhz({})"
                            .format(self._ui_input_elements["setpoint_step"].value()))
                self._rpc_client.setpoint_step_mhz(self._ui_input_elements["setpoint_step"].value())
        setpoint_step_button.clicked.connect(setpoint_step)
        column3.addWidget(setpoint_step_button, 1, 0, Qt.AlignLeft)
        self._ui_input_elements.update({"setpoint_step": QDoubleSpinBox()})
        self._ui_input_elements["setpoint_step"].setDecimals(5)
        self._ui_input_elements["setpoint_step"].setRange(-1e4, 1e4)
        self._ui_input_elements["setpoint_step"].setSingleStep(1.)
        self._ui_input_elements["setpoint_step"].setSuffix(" MHz")
        self._ui_input_elements["setpoint_step"].setValue(10.)
        column3.addWidget(self._ui_input_elements["setpoint_step"], 1, 1, Qt.AlignLeft)

        set_output_button = QPushButton("Set &output")

        def set_output():
            if self._rpc_client is not None:
                logger.info("sending RPC set_output({})"
                            .format(self._ui_input_elements["output"].value()))
                self._rpc_client.set_output(self._ui_input_elements["output"].value())
        set_output_button.clicked.connect(set_output)
        column3.addWidget(set_output_button, 2, 0, Qt.AlignLeft)
        self._ui_input_elements.update({"output": QDoubleSpinBox()})
        self._ui_input_elements["output"].setDecimals(5)
        self._ui_input_elements["output"].setRange(-1e4, 1e4)
        self._ui_input_elements["output"].setSingleStep(0.1)
        if self._rpc_client is not None:
            self._ui_input_elements["output"].setSuffix(" {}".format(self.output_unit))
        self._ui_input_elements["output"].setValue(self.status_dict["output_offset"])
        column3.addWidget(self._ui_input_elements["output"], 2, 1, Qt.AlignLeft)

        set_output_offset_button = QPushButton("Set output offset")

        def set_output_offset():
            if self._rpc_client is not None:
                logger.info("sending RPC set_output_offset({})"
                            .format(self._ui_input_elements["output_offset"].value()))
                self._rpc_client.set_output_offset(self._ui_input_elements["output_offset"].value())
        set_output_offset_button.clicked.connect(set_output_offset)
        column3.addWidget(set_output_offset_button, 3, 0, Qt.AlignLeft)
        self._ui_input_elements.update({"output_offset": QDoubleSpinBox()})
        self._ui_input_elements["output_offset"].setDecimals(5)
        self._ui_input_elements["output_offset"].setRange(-1e4, 1e4)
        self._ui_input_elements["output_offset"].setSingleStep(0.1)
        if self._rpc_client is not None:
            self._ui_input_elements["output_offset"].setSuffix(" {}".format(self.output_unit))
        self._ui_input_elements["output_offset"].setValue(self.status_dict["output_offset"])
        column3.addWidget(self._ui_input_elements["output_offset"], 3, 1, Qt.AlignLeft)

        column3_gb.setLayout(column3)
        layout.addWidget(column3_gb)

        widget = QWidget()
        widget.setLayout(layout)
        self.v_splitter.addWidget(widget)

    def _build_scan_ctrl(self):
        layout = QHBoxLayout()

        start_scan_gb = QGroupBox("")
        start_scan_gb_layout = QFormLayout()

        scan_mode_dict = {
            "ramp to center": 0,
            "ramp to min": 1,
            "ramp to max": 2,
            "triangle /\\/\\/\\...": 3,
            "triangle \\/\\/\\/...": 4,
            "sawtooth |/|/|/...": 5,
            "sawtooth /|/|/|...": 6,
            "sawtooth |\\|\\|\\...": 7,
            "sawtooth \\|\\|\\|...": 8}
        start_scan_button = QPushButton("St&art scan")

        def start_scan():
            waveform = scan_mode_dict[self._ui_input_elements["scan_waveform"].currentText()]
            rate = self._ui_input_elements["scan_rate"].value()
            f_upper = self._ui_input_elements["scan_f_upper"].value()
            f_lower = self._ui_input_elements["scan_f_lower"].value()
            timestep = self._ui_input_elements["scan_timestep"].value()
            if self._rpc_client is not None:
                logger.info("sending RPC start_scan(waveform={}, rate={}, f_lower={}, f_upper={}, timestep={})"
                            .format(waveform, rate, f_lower, f_upper, timestep))
                self._rpc_client.start_scan(waveform, rate, f_lower, f_upper, timestep)
        start_scan_button.clicked.connect(start_scan)
        start_scan_gb_layout.addWidget(start_scan_button)

        self._ui_input_elements.update({"scan_waveform": QComboBox()})
        for k, _ in scan_mode_dict.items():
            self._ui_input_elements["scan_waveform"].addItem(k)
        start_scan_gb_layout.addRow("Waveform", self._ui_input_elements["scan_waveform"])

        self._ui_input_elements.update({"scan_rate": QDoubleSpinBox()})
        self._ui_input_elements["scan_rate"].setDecimals(5)
        self._ui_input_elements["scan_rate"].setRange(-1e4, 1e4)
        self._ui_input_elements["scan_rate"].setSingleStep(1.)
        self._ui_input_elements["scan_rate"].setValue(10.)
        self._ui_input_elements["scan_rate"].setSuffix(" MHz/s")
        start_scan_gb_layout.addRow("Rate", self._ui_input_elements["scan_rate"])

        self._ui_input_elements.update({"scan_f_upper": QDoubleSpinBox()})
        self._ui_input_elements["scan_f_upper"].setDecimals(5)
        self._ui_input_elements["scan_f_upper"].setRange(-1e4, 1e4)
        self._ui_input_elements["scan_f_upper"].setSingleStep(1.)
        self._ui_input_elements["scan_f_upper"].setValue(100.)
        self._ui_input_elements["scan_f_upper"].setSuffix(" MHz")
        start_scan_gb_layout.addRow("Upper freq", self._ui_input_elements["scan_f_upper"])

        self._ui_input_elements.update({"scan_f_lower": QDoubleSpinBox()})
        self._ui_input_elements["scan_f_lower"].setDecimals(5)
        self._ui_input_elements["scan_f_lower"].setRange(-1e4, 1e4)
        self._ui_input_elements["scan_f_lower"].setSingleStep(1.)
        self._ui_input_elements["scan_f_lower"].setValue(-100.)
        self._ui_input_elements["scan_f_lower"].setSuffix(" MHz")
        start_scan_gb_layout.addRow("Lower freq", self._ui_input_elements["scan_f_lower"])

        self._ui_input_elements.update({"scan_timestep": QDoubleSpinBox()})
        self._ui_input_elements["scan_timestep"].setDecimals(5)
        self._ui_input_elements["scan_timestep"].setRange(0.001, 1e4)
        self._ui_input_elements["scan_timestep"].setSingleStep(0.01)
        self._ui_input_elements["scan_timestep"].setValue(.1)
        self._ui_input_elements["scan_timestep"].setSuffix(" s")
        start_scan_gb_layout.addRow("Time step", self._ui_input_elements["scan_timestep"])

        start_scan_gb.setLayout(start_scan_gb_layout)
        layout.addWidget(start_scan_gb)

        column2_gb = QGroupBox("")
        column2 = QVBoxLayout()

        column2.addWidget(self.scanning_indicator)
        self.scanning_indicator.setCheckState(self.status_dict["scanning"])

        stop_scan_button = QPushButton("St&op scan")

        def stop_scan():
            if self._rpc_client is not None:
                logger.info("sending RPC stop_scan()")
                self._rpc_client.stop_scan()
        stop_scan_button.clicked.connect(stop_scan)
        column2.addWidget(stop_scan_button)

        column2_gb.setLayout(column2)
        layout.addWidget(column2_gb)

        measure_output_sensitivity_gb = QGroupBox("Measure output sensitivity")
        measure_output_sensitivity_gb_layout = QFormLayout()

        measure_output_sensitivity_button = QPushButton("Measure")

        def measure_output_sensitivity():
            lower_value = self._ui_input_elements["output_sensitivity_lower_value"].value()
            upper_value = self._ui_input_elements["output_sensitivity_upper_value"].value()
            averaging_time = self._ui_input_elements["output_sensitivity_averaging_time"].value()
            settle_time = self._ui_input_elements["output_sensitivity_settle_time"].value()
            if self._rpc_client is not None:
                logger.info("sending RPC measure_output_sensitivity("
                            "lower_value={}, upper_value={}, averaging_time={}, settle_time={})"
                            .format(lower_value, upper_value, averaging_time, settle_time))
                self._rpc_client.measure_output_sensitivity(lower_value, upper_value, averaging_time, settle_time)

        measure_output_sensitivity_button.clicked.connect(measure_output_sensitivity)
        measure_output_sensitivity_gb_layout.addWidget(measure_output_sensitivity_button)

        self._ui_input_elements.update({"output_sensitivity_lower_value": QDoubleSpinBox()})
        self._ui_input_elements["output_sensitivity_lower_value"].setDecimals(5)
        self._ui_input_elements["output_sensitivity_lower_value"].setRange(-1e4, 1e4)
        self._ui_input_elements["output_sensitivity_lower_value"].setSingleStep(0.01)
        self._ui_input_elements["output_sensitivity_lower_value"].setSuffix(" " + self.output_unit)
        self._ui_input_elements["output_sensitivity_lower_value"].setValue(self.status_dict["output_offset"] - 0.5)
        measure_output_sensitivity_gb_layout.addRow("Lower value",
                                                    self._ui_input_elements["output_sensitivity_lower_value"])

        self._ui_input_elements.update({"output_sensitivity_upper_value": QDoubleSpinBox()})
        self._ui_input_elements["output_sensitivity_upper_value"].setDecimals(5)
        self._ui_input_elements["output_sensitivity_upper_value"].setRange(-1e4, 1e4)
        self._ui_input_elements["output_sensitivity_upper_value"].setSingleStep(0.01)
        self._ui_input_elements["output_sensitivity_upper_value"].setSuffix(" " + self.output_unit)
        self._ui_input_elements["output_sensitivity_upper_value"].setValue(self.status_dict["output_offset"] + 0.5)
        measure_output_sensitivity_gb_layout.addRow("Upper value",
                                                    self._ui_input_elements["output_sensitivity_upper_value"])

        self._ui_input_elements.update({"output_sensitivity_averaging_time": QDoubleSpinBox()})
        self._ui_input_elements["output_sensitivity_averaging_time"].setDecimals(5)
        self._ui_input_elements["output_sensitivity_averaging_time"].setRange(0.1, 1e4)
        self._ui_input_elements["output_sensitivity_averaging_time"].setSingleStep(0.1)
        self._ui_input_elements["output_sensitivity_averaging_time"].setValue(5)
        self._ui_input_elements["output_sensitivity_averaging_time"].setSuffix(" s")
        measure_output_sensitivity_gb_layout.addRow("Avg. time",
                                                    self._ui_input_elements["output_sensitivity_averaging_time"])

        self._ui_input_elements.update({"output_sensitivity_settle_time": QDoubleSpinBox()})
        self._ui_input_elements["output_sensitivity_settle_time"].setDecimals(5)
        self._ui_input_elements["output_sensitivity_settle_time"].setRange(0.1, 1e4)
        self._ui_input_elements["output_sensitivity_settle_time"].setSingleStep(0.1)
        self._ui_input_elements["output_sensitivity_settle_time"].setValue(5)
        self._ui_input_elements["output_sensitivity_settle_time"].setSuffix(" s")
        measure_output_sensitivity_gb_layout.addRow("Settling time",
                                                    self._ui_input_elements["output_sensitivity_settle_time"])

        measure_output_sensitivity_gb.setLayout(measure_output_sensitivity_gb_layout)
        layout.addWidget(measure_output_sensitivity_gb)

        widget = QWidget()
        widget.setLayout(layout)
        self.v_splitter.addWidget(widget)

    def _build_extra_info(self):
        outer_layout = QHBoxLayout()  # just for cosmetic reasons (to fit width of other segments)
        extra_info_gb = QGroupBox("Current values:")
        layout = QHBoxLayout()

        column1 = QFormLayout()
        column1.addRow("Setpoint (incl. scan)", self.setpoint_display)
        column1.addRow("Setpoint (no scan)", self.setpoint_noscan_display)
        column1.addRow("cp", self.cp_display)
        column1.addRow("ci", self.ci_display)
        layout.addLayout(column1)

        column2 = QFormLayout()
        column2.addRow("Output offset", self.output_offset_display)
        column2.addRow("Integrator", self.integrator_display)
        column2.addRow("Output", self.output_display)
        column2.addRow("Output sensitivity (if known)", self.output_sensitivity_display)

        layout.addLayout(column2)
        extra_info_gb.setLayout(layout)
        outer_layout.addWidget(extra_info_gb)

        widget = QWidget()
        widget.setLayout(outer_layout)
        self.v_splitter.addWidget(widget)

    def _build_aux_ctrl(self):
        outer_layout = QHBoxLayout()  # just for cosmetic reasons (to fit width of other segments)
        aux_output_name = "N/A"
        if self._rpc_client is not None:
            aux_output_name = self._rpc_client.get_aux_output_name()
        aux_ctrl_gb = QGroupBox("Auxiliary output control: {}".format(aux_output_name))
        layout = QHBoxLayout()

        column1 = QFormLayout()
        column1.addRow("Current value", self.aux_output_display)
        layout.addLayout(column1)

        column2 = QGridLayout()

        set_aux_output_button = QPushButton("Set aux output")

        def set_aux_output():
            if self._rpc_client is not None:
                logger.info("sending RPC set_aux_output({})"
                            .format(self._ui_input_elements["aux_output_set"].value()))
                self._rpc_client.set_aux_output(self._ui_input_elements["aux_output_set"].value())

        set_aux_output_button.clicked.connect(set_aux_output)
        column2.addWidget(set_aux_output_button, 0, 0, Qt.AlignLeft)
        self._ui_input_elements.update({"aux_output_set": QDoubleSpinBox()})
        self._ui_input_elements["aux_output_set"].setDecimals(5)
        self._ui_input_elements["aux_output_set"].setRange(-1e4, 1e4)
        self._ui_input_elements["aux_output_set"].setSingleStep(0.1)
        if self._rpc_client is not None:
            self._ui_input_elements["aux_output_set"].setSuffix(" {}".format(self.aux_output_unit))
        self._ui_input_elements["aux_output_set"].setValue(self.status_dict["aux_output"])
        column2.addWidget(self._ui_input_elements["aux_output_set"], 0, 1, Qt.AlignLeft)

        layout.addLayout(column2)
        aux_ctrl_gb.setLayout(layout)
        outer_layout.addWidget(aux_ctrl_gb)

        widget = QWidget()
        widget.setLayout(outer_layout)
        self.v_splitter.addWidget(widget)

    def _subscriber_init_cb(self, data):
        self.status_dict = data
        if self._enable_stripchart:
            self.stripchart.set_alarm(self.status_dict["output_rail_warning"])
        if self._enable_lcd:
            self._update_lcd()
        if self._enable_extra_info:
            self.setpoint_display.setText("{:.8f} nm".format(self.status_dict["setpoint"]))
            self.setpoint_noscan_display.setText("{:.8f} nm".format(self.status_dict["setpoint_noscan"]))
            self.cp_display.setText("{:0.4f} {}/nm".format(self.status_dict["cp"], self.output_unit))
            self.ci_display.setText("{:0.4f} {}/(nm*ms)".format(self.status_dict["ci"], self.output_unit))
            self.output_offset_display.setText("{:0.7f} {}".format(self.status_dict["output_offset"], self.output_unit))
            self.integrator_display.setText("{:0.7f} {}".format(self.status_dict["integrator"], self.output_unit))
            self.output_display.setText("{:0.7f} {}".format(self.status_dict["output"], self.output_unit))
            self.output_sensitivity_display.setText("{:0.7f} nm/{}".format(self.status_dict["output_sensitivity"],
                                                                           self.output_unit)
                                                    if self.status_dict["output_sensitivity"] != 0 else "")
        if self._enable_aux_ctrl:
            self.aux_output_display.setText("{:0.7f} {}".format(self.status_dict["aux_output"],
                                                                self.aux_output_unit))
        if self._enable_interferograms:
            self.interferograms.update_data()

        return data

    def _subscriber_mod_cb(self, mod):
        try:
            if mod["key"] == "latest_value":
                if self._enable_stripchart:
                    self._add_stripchart_point()
                if self._enable_lcd:
                    self._update_lcd()
                if self._enable_interferograms:
                    self.interferograms.update_data()
            elif mod["key"] == "output_rail_warning":
                self.stripchart.set_alarm(self.status_dict["output_rail_warning"])
            elif mod["key"] == "locked":
                self.lock_indicator.setCheckState(self.status_dict["locked"])
            elif mod["key"] == "scanning":
                self.scanning_indicator.setCheckState(self.status_dict["scanning"])
            elif mod["key"] == "setpoint":
                self.setpoint_display.setText("{:.8f} nm".format(self.status_dict["setpoint"]))
            elif mod["key"] == "setpoint_noscan":
                self.setpoint_noscan_display.setText("{:.8f} nm".format(self.status_dict["setpoint_noscan"]))
            elif mod["key"] == "cp":
                self.cp_display.setText("{:0.4f} {}/nm".format(self.status_dict["cp"], self.output_unit))
            elif mod["key"] == "ci":
                self.ci_display.setText("{:0.4f} {}/(nm*ms)".format(self.status_dict["ci"], self.output_unit))
            elif mod["key"] == "output_offset":
                self.output_offset_display.setText("{:0.7f} {}".format(self.status_dict["output_offset"],
                                                                       self.output_unit))
            elif mod["key"] == "integrator":
                self.integrator_display.setText("{:0.7f} {}".format(self.status_dict["integrator"], self.output_unit))
            elif mod["key"] == "output":
                self.output_display.setText("{:0.7f} {}".format(self.status_dict["output"], self.output_unit))
            elif mod["key"] == "aux_output":
                self.aux_output_display.setText("{:0.7f} {}".format(self.status_dict["aux_output"],
                                                                    self.aux_output_unit))
            elif mod["key"] == "output_sensitivity":
                self.output_sensitivity_display.setText("{:0.7f} nm/{}".format(self.status_dict["output_sensitivity"],
                                                                               self.output_unit)
                                                        if self.status_dict["output_sensitivity"] != 0 else "")
        except KeyError:
            pass

    keepalive_interval = 3600.  # ping RPC server after this interval (in s) to keep the connection alive

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
    parser = ArgumentParser(description="Wavemeter PI lock remote control")
    group = parser.add_argument_group("network arguments")
    group.add_argument("-s", "--server", default="::1", help="address of the host running the lock client to control"
                                                             " (default: ::1 (localhost))")
    group.add_argument("--rpc_port", type=int, default=3284, help="lock client RPC server port (default: 3284)")
    group.add_argument("--rpc_target", help="name of the RPC target (e.g. 'lock_client_ch1')")
    group.add_argument("--pub_port", type=int, default=3282, help="lock client publisher port (default: 3282)")
    group.add_argument("--notifier", default="", help="name of the notifier (default: same as rpc_target)")

    group = parser.add_argument_group("window options")
    group.add_argument("--title", default=None, help="window title")
    group.add_argument("--width", type=int, default=611, help="window width in pixels (default:611 (minimum if all "
                                                              "elements are enabled)")
    group.add_argument("-x", type=int, default=0, help="window x position in pixels (default: 0)")
    group.add_argument("-y", type=int, default=0, help="window y position in pixels (default: 0)")

    group = parser.add_argument_group("plot options")
    parser.set_defaults(no_plot=False, plot_wl=False)
    group.add_argument("--no_plot", dest="no_plot", action="store_true", help="disable plot")
    group.add_argument("--plot_height", type=int, default=350, help="plot height in pixels (default: 350)")
    group.add_argument("--plot_numpoints", type=int, default=100, help="number of points in plot (default: 100)")
    group.add_argument("--plot_wl", dest="plot_wl", action="store_true", help="plot wavelength (instead of frequency)")
    group.add_argument("--plot_offset", type=float, default=0., help="offset of the stripchart traces (in MHz or nm, "
                                                                     "depending on whether --plot_wl is set)")
    group.add_argument("--plot_actual_color", default="k", help="color of the plot of the actual wavelength / frequency"
                                                                " (default: k, other examples are r, g, b, #00ff00 - "
                                                                "see pyqtgraph.mkColor documentation for more info)")
    group.add_argument("--plot_actual_width", type=int, default=3, help="width of the actual wavelength / frequency "
                                                                        "trace (default: 3)")
    group.add_argument("--plot_actual_symbol", default="", help="symbol for actual wavelength / frequency plot points"
                                                                " (default: None, examples are o, s, t, + - see"
                                                                " pyqtgrqph documentation for details)")
    group.add_argument("--plot_setpoint_color", default="g", help="color of the plot of the setpoint trace"
                                                                  " (default: g)")
    group.add_argument("--plot_setpoint_width", type=int, default=3, help="width of the setpoint trace (default: 3)")
    group.add_argument("--plot_setpoint_symbol", default="", help="symbol for setpoint plot points (default: None)")
    group.add_argument("--plot_bgr", default="w", help="plot background color (default: w)")
    group.add_argument("--plot_bgr_alarm", default="r", help="plot background color is the output is (close to) railing"
                                                             " (default: r)")

    group = parser.add_argument_group("number display options")
    parser.set_defaults(no_lcd=False, lcd_freq=False)
    group.add_argument("--no_number", dest="no_lcd", action="store_true", help="disable number display")
    group.add_argument("--number_height", type=int, default=200, help="number height in pixels (default: 200)")
    group.add_argument("--number_digits", type=int, default=9, help="number of displayed digits (default: 9,"
                                                                    " includes digits before decimal point)")
    group.add_argument("--number_freq", dest="lcd_freq", action="store_true", help="display frequency (instead "
                                                                                   "of wavelength)")

    group = parser.add_argument_group("interferogram options")
    parser.set_defaults(interferograms=False, if_update_control=False, if_exp_control=False)
    group.add_argument("-i", "--interferograms", dest="interferograms", action="store_true", help="show interferograms")
    group.add_argument("--iplot_exp_ctrl", dest="if_exp_control", action="store_true", help="show exposure time"
                                                                                            " controls")
    group.add_argument("--iplot_interval", type=int, default=1, help="# values between interferogram updates")
    group.add_argument("--iplot_interval_ctrl", dest="if_update_control", action="store_true", help="show interferogram"
                                                                                                    " update rate"
                                                                                                    " control")
    group.add_argument("--iplot_color", default="g", help="color of the 1st interferogram  trace (default: g)")
    group.add_argument("--iplot_width", type=int, default=1, help="width of the 1st interferogram trace (default: 1)")
    group.add_argument("--iplot_color2", default="b", help="color of the 2nd interferogram  trace (default: b)")
    group.add_argument("--iplot_width2", type=int, default=1, help="width of the 2nd interferogram trace (default: 1)")
    group.add_argument("--iplot_bgr", default="w", help="interferogram plot background color (default: w)")
    group.add_argument("--iplot_height", type=int, default=150, help="interferogram plot height in pixels "
                                                                     "(default: 150)")

    group.add_argument("--wavemeter_host", default="", help="address of the host running the wavemeter RPC server "
                                                            "(default: same as lock client host)")
    group.add_argument("--wavemeter_rpc_port", type=int, default=3280, help="wavemeter server RPC port (default: 3280)")
    group.add_argument("--wavemeter_rpc_target", default="wavemeter_server",
                       help="wavemeter server RPC target (default: \"wavemeter_server\")")

    group = parser.add_argument_group("further GUI elements")
    parser.set_defaults(no_lock_ctrl=False, no_scan_ctrl=False, no_extra_info=False, aux_ctrl=False)
    group.add_argument("--no_lock_ctrl", dest="no_lock_ctrl", action="store_true", help="disable lock controls")
    group.add_argument("--no_scan_ctrl", dest="no_scan_ctrl", action="store_true", help="disable scan controls")
    group.add_argument("--no_info", dest="no_extra_info", action="store_true", help="disable additional info")
    group.add_argument("--aux", dest="aux_ctrl", action="store_true", help="enable auxiliary output control")

    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)
    notifier = args.notifier
    if args.notifier == "":
        logger.info("No notifier specified, trying same name as RPC target: {}".format(args.rpc_target))
        notifier = args.rpc_target
    wavemeter_host = args.server if args.wavemeter_host == "" else args.wavemeter_host
    actual_value_symbol = args.plot_actual_symbol if args.plot_actual_symbol != "" else None
    setpoint_symbol = args.plot_setpoint_symbol if args.plot_setpoint_symbol != "" else None
    wlr = WavemeterLockRemote(host=args.server, rpc_target=args.rpc_target, rpc_port=args.rpc_port,
                              notifier_name=notifier, notifier_port=args.pub_port, wavemeter_host=wavemeter_host,
                              wavemeter_rpc_port=args.wavemeter_rpc_port,
                              wavemeter_rpc_target=args.wavemeter_rpc_target, title=args.title, window_width=args.width,
                              window_x=args.x, window_y=args.y, enable_stripchart=not args.no_plot,
                              stripchart_height=args.plot_height, stripchart_num_points=args.plot_numpoints,
                              plot_freq=not args.plot_wl, plot_offset=args.plot_offset,
                              plot_pen_actual=pyqtgraph.mkPen(color=args.plot_actual_color,
                                                              width=args.plot_actual_width),
                              plot_symbol_actual=actual_value_symbol,
                              plot_pen_setpoint=pyqtgraph.mkPen(color=args.plot_setpoint_color,
                                                                width=args.plot_setpoint_width),
                              plot_symbol_setpoint=setpoint_symbol, plot_bgr=args.plot_bgr,
                              plot_bgr_alarm=args.plot_bgr_alarm, enable_lcd=not args.no_lcd,
                              lcd_height=args.number_height, lcd_ndigits=args.number_digits,  lcd_freq=args.lcd_freq,
                              enable_lock_ctrl=not args.no_lock_ctrl, enable_scan_ctrl=not args.no_scan_ctrl,
                              enable_extra_info=not args.no_extra_info, enable_aux_ctrl=args.aux_ctrl,
                              enable_interferograms=args.interferograms,
                              interferogram_update_interval=args.iplot_interval,
                              show_interferogram_update_control=args.if_update_control,
                              if0_pen=pyqtgraph.mkPen(color=args.iplot_color, width=args.iplot_width),
                              if1_pen=pyqtgraph.mkPen(color=args.iplot_color2, width=args.iplot_width2),
                              interferogram_bgr=args.iplot_bgr, interferogram_height=args.iplot_height,
                              interferogram_exposure_control=args.if_exp_control)
    wlr.run()


if __name__ == "__main__":
    main()
