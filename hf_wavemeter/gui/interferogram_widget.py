from typing import Any

from PyQt5.QtWidgets import (
    QWidget,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox)

import pyqtgraph

from sipyco.pc_rpc import BestEffortClient

import logging
logger = logging.getLogger(__name__)


class InterferogramWidget(QWidget):
    """
    Widget which connects to a :class:`WavemeterServer` and displays its interferograms. Shows the plot as well as an
    optional integer control allowing updates to be limited to every nth call to :meth:`update_data` (or entirely
    disabled by entering negative values for n).

    :param rpc_host: host of the :class:`WavemeterServer`
    :param rpc_port: port of the :class:`WavemeterServer`
    :param rpc_target: :class:`WavemeterServer` target name
    :param channel_id: channel number for which to display the interferograms
    :param if0_pen: pen for the 1st interferogram
    :param if1_pen: pen for the 2nd interferogram
    :param bgr_color: background color
    :param update_interval: number of calls to :meth:`update_data` after which to actually update
    :param show_interval_spinbox: display a GUI element to change the `update_interval` during operation
    :param show_exposure_ctrl: show controls for wavemeter exposure times
    """
    def __init__(self, rpc_host: str = "::1", rpc_port: int = 3280, rpc_target: str = "wavemeter_server",
                 channel_id: int = 1, if0_pen: Any = pyqtgraph.mkPen(color="g", width=1),
                 if1_pen: Any = pyqtgraph.mkPen(color="b", width=1), bgr_color: str = "w",
                 update_interval: int = 1, show_interval_spinbox: bool = False, show_exposure_ctrl: bool = False,
                 **kwargs):
        super().__init__(**kwargs)
        self.if_plot = pyqtgraph.PlotWidget(background=bgr_color)

        self.if0_pen = if0_pen
        self.if1_pen = if1_pen

        self._rpc_client = BestEffortClient(rpc_host, rpc_port, rpc_target)
        self.channel_id = channel_id

        layout = QVBoxLayout()
        layout.addWidget(self.if_plot)

        self.update_interval_spinbox = QDoubleSpinBox()
        self.update_interval_spinbox.setDecimals(0)
        self.update_interval_spinbox.setRange(-1, 1e10)
        self.update_interval_spinbox.setValue(update_interval)
        if show_interval_spinbox:
            update_interval_layout = QFormLayout()
            update_interval_layout.addRow("# values between updates (-1 to disable)", self.update_interval_spinbox)
            update_int_gb = QGroupBox()
            update_int_gb.setLayout(update_interval_layout)
            layout.addWidget(update_int_gb)

        if show_exposure_ctrl:
            exposure_gb = QGroupBox("Exposure")
            exposure_gb_layout = QHBoxLayout()
            exposure_gb.setLayout(exposure_gb_layout)

            control_form = QFormLayout()

            self._exp_time1 = QDoubleSpinBox()
            self._exp_time1.setRange(1, 2000)
            self._exp_time1.setSingleStep(1)
            self._exp_time1.setDecimals(0)
            self._exp_time1.setSuffix(" ms")
            self._exp_time1.setValue(1)
            control_form.addRow("T1", self._exp_time1)

            exposure_gb_layout.addLayout(control_form)
            control_form2 = QFormLayout()

            self._exp_time2 = QDoubleSpinBox()
            self._exp_time2.setRange(0, 2000)
            self._exp_time2.setSingleStep(1)
            self._exp_time2.setDecimals(0)
            self._exp_time2.setSuffix(" ms")
            self._exp_time2.setValue(1)
            control_form2.addRow("T2", self._exp_time2)

            exposure_gb_layout.addLayout(control_form2)

            self._exp_auto = QCheckBox()
            self._exp_auto.setText("Auto adj.")
            exposure_gb_layout.addWidget(self._exp_auto)

            exposure_get_button = QPushButton("Get")

            def exposure_get():
                if self._rpc_client is not None:
                    logger.info("sending RPC get_exposure_time({}, 1)".format(self.channel_id))
                    time1 = self._rpc_client.get_exposure_time(self.channel_id, 1)
                    logger.info("sending RPC get_exposure_time({}, 2)".format(self.channel_id))
                    time2 = self._rpc_client.get_exposure_time(self.channel_id, 2)
                    logger.info("sending RPC get_exposure_auto_adjust({})".format(self.channel_id))
                    auto = self._rpc_client.get_exposure_auto_adjust(self.channel_id)
                    self._exp_time1.setValue(time1)
                    self._exp_time2.setValue(time2)
                    self._exp_auto.setChecked(auto)

            exposure_get_button.clicked.connect(exposure_get)

            exposure_gb_layout.addWidget(exposure_get_button)
            exposure_set_button = QPushButton("Set")

            def exposure_set():
                time1 = int(self._exp_time1.value())
                time2 = int(self._exp_time2.value())
                auto = bool(self._exp_auto.isChecked())
                if self._rpc_client is not None:
                    logger.info("sending RPC set_exposure_time({}, 1, {})".format(self.channel_id, time1))
                    self._rpc_client.set_exposure_time(self.channel_id, 1, time1)
                    logger.info("sending RPC set_exposure_time({}, 2, {})".format(self.channel_id, time2))
                    self._rpc_client.set_exposure_time(self.channel_id, 2, time2)
                    logger.info("sending RPC set_exposure_auto_adjust({}, {})".format(self.channel_id, auto))
                    self._rpc_client.set_exposure_auto_adjust(self.channel_id, auto)

            exposure_set_button.clicked.connect(exposure_set)

            exposure_gb_layout.addWidget(exposure_set_button)

            layout.addWidget(exposure_gb)
            exposure_get()

        self.setLayout(layout)
        self.value_counter = update_interval + 1

    def update_data(self):
        self.value_counter += 1
        if self.value_counter > self.update_interval_spinbox.value() > 0 and self._rpc_client is not None:
            if_0 = self._rpc_client.get_interferogram(self.channel_id, 0)
            if_1 = self._rpc_client.get_interferogram(self.channel_id, 1)
            if if_0 is not None and if_1 is not None:
                self.if_plot.clear()
                self.if_plot.plot(if_0, pen=self.if0_pen)
                self.if_plot.plot(if_1, pen=self.if1_pen)
            self.value_counter = 0
