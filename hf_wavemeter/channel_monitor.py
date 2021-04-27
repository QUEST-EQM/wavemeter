import sys
from PyQt5.QtWidgets import (
    QApplication,
    QLCDNumber,
    QWidget,
    QPushButton,
    QCheckBox,
    QVBoxLayout,
    QSplitter)
from PyQt5.QtCore import Qt

from sipyco.common_args import verbosity_args, init_logger_from_args

import asyncio
from asyncqt import QEventLoop

from hf_wavemeter.client import WavemeterClient

from hf_wavemeter.gui.stripchart import Stripchart
from hf_wavemeter.gui.interferogram_widget import InterferogramWidget
import pyqtgraph

from typing import Any

from argparse import ArgumentParser

import logging
logger = logging.getLogger(__name__)


class WavemeterChannelMonitor(WavemeterClient):
    """
    Connects to a :class:`WavemeterServer` to monitor a channel wavelength, temperature or pressure. There are three
    available GUI elements: a stripchart plot, a scalable number display, and an interferogram plot.

    :param host: the address at which the :class:`WavemeterServer` publisher is running
    :param channel: wavemeter channel (can be either <n>, '<n>', 'ch<n>', 'T' or 'p')
    :param port: port of the publisher
    :param rpc_host: host of the wavemeter server (only needed for interferograms, default = same as publisher)
    :param rpc_port: port of the wavemeter server rpc target (only needed for interferograms)
    :param rpc_target: target name for rpcs (only needed for interferograms)
    :param title: the window title (generated from the server details by default)
    :param window_width: initial width of the window in pixels
    :param window_x: initial x position of the window in pixels
    :param window_y: initial y position of the window in pixels
    :param enable_stripchart: show the stripchart
    :param stripchart_height: initial stripchart height in pixels
    :param stripchart_num_points: number of data points displayed in the stripchart
    :param plot_freq: True for frequency plot, False for wavelength plot
    :param plot_offset: y offset of the plot (unit is MHz for frequencies and nm for wavelengths)
    :param plot_pen: pen specification for the plot trace (see pyqtgraph documentation)
    :param plot_symbol: symbol specification for the plot points
    :param plot_bgr: bgr color of the plot
    :param enable_lcd: show the current value (wavelength or frequency) as a scalable number
    :param lcd_height: lcd height in pixels
    :param lcd_ndigits: number of digits in the lcd
    :param lcd_freq: start with the number display showing the frequency (default is wavelength)
    :param enable_interferograms: display the interferograms
    :param interferogram_update_interval: number of values after which to update the interferograms
    :param show_interferogram_update_control: show the spinbox to adjust interferogram update rate
    :param if0_pen: pen specification for the 1st interferogram
    :param if1_pen: pen specification for the 2nd interferogram
    :param interferogram_bgr: bgr color of the interferogram plot
    :param interferogram_height: initial height of the interferogram plot in pixels
    :param interferogram_exposure_control: enable control of wavemeter exposure times
    """

    def __init__(self, host: str = "::1", channel: str = "ch1",
                 port: int = 3281, rpc_host: str = "", rpc_port: int = 3280, rpc_target="wavemeter_server",
                 title: str = None, window_width: int = 611, window_x: int = 0, window_y: int = 0,
                 enable_stripchart: bool = True, stripchart_height: int = 350, stripchart_num_points: int = 100,
                 plot_freq: bool = True, plot_offset: float = 0., plot_pen: Any = pyqtgraph.mkPen(color="k", width=3),
                 plot_symbol: Any = None, plot_bgr: str = "w", enable_lcd: bool = True, lcd_height: int = 160,
                 lcd_ndigits: int = 10, lcd_freq: bool = False, enable_interferograms: bool = False,
                 interferogram_update_interval: int = 1, show_interferogram_update_control: bool = False,
                 if0_pen: Any = pyqtgraph.mkPen(color="g", width=1),
                 if1_pen: Any = pyqtgraph.mkPen(color="b", width=1), interferogram_bgr: str = "w",
                 interferogram_height: int = 150, interferogram_exposure_control: bool = False):

        # user interface components
        self._enable_stripchart = enable_stripchart
        self._enable_lcd = enable_lcd
        self._enable_interferograms = enable_interferograms

        self.lcd_ndigits = lcd_ndigits

        self.stripchart_pen = plot_pen
        self.stripchart_symbol = plot_symbol
        self.stripchart_bgr = plot_bgr

        self._app = QApplication(sys.argv)
        self._loop = QEventLoop(self._app)
        asyncio.set_event_loop(self._loop)

        self.plot_freq = plot_freq
        self.plot_offset = plot_offset

        self.lcd_freq = lcd_freq

        try:  # accept integer (or string lacking the "ch" prefix) as channel argument
            self.channel = "ch{}".format(int(channel))
        except ValueError:
            self.channel = channel

        self.not_a_wavelength = (self.channel in ["T", "p"])  # disable frequency conversion options for T and p
        if self.not_a_wavelength:
            self.plot_freq = False
            self.lcd_freq = False
            self._enable_interferograms = False

        self.title = title if title is not None else "{} monitor ({}:{})".format(channel, host, port)

        if self._enable_interferograms and not self.not_a_wavelength:
            channel_id = int(self.channel[2:])
            self.interferograms = InterferogramWidget(rpc_host=rpc_host, rpc_port=rpc_port, rpc_target=rpc_target,
                                                      channel_id=channel_id, if0_pen=if0_pen, if1_pen=if1_pen,
                                                      bgr_color=interferogram_bgr,
                                                      update_interval=interferogram_update_interval,
                                                      show_interval_spinbox=show_interferogram_update_control,
                                                      show_exposure_ctrl=interferogram_exposure_control)

        self._build_ui(window_width, window_x, window_y, stripchart_height, stripchart_num_points, lcd_height,
                       interferogram_height)

        super().__init__(self.channel, host, port, self._loop)

    def _build_ui(self, window_width, window_x, window_y, stripchart_height, stripchart_num_points, lcd_height,
                  interferogram_height):
        self.window = QWidget()
        self.window.setWindowTitle(self.title)
        window_height = stripchart_height * self._enable_stripchart \
            + lcd_height * self._enable_lcd \
            + interferogram_height * self._enable_interferograms
        self.window.resize(window_width, window_height)
        self.window.move(window_x, window_y)

        # The layout only contains one element (the splitter), but it is needed to rescale widgets as window is rescaled
        self.v_layout = QVBoxLayout()
        self.v_splitter = QSplitter(Qt.Vertical)
        self.v_layout.addWidget(self.v_splitter)

        self.window.setLayout(self.v_layout)
        self.v_layout.addWidget(self.v_splitter)

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

        self.v_splitter.setSizes(splitter_sizes)

    def _build_stripchart(self, num_points):
        plot_title = ""
        if self.plot_freq:
            plot_y_label = "frequency (MHz)"
            if self.plot_offset != 0:
                plot_title = "offset: {:11} THz".format(self.plot_offset * 1e-6)
        else:
            if self.channel == "T":
                plot_y_label = "temperature (degrees C)"
                if self.plot_offset != 0:
                    plot_title = "offset: {:5} degrees C".format(self.plot_offset)
            elif self.channel == "p":
                plot_y_label = "pressure (mbar)"
                if self.plot_offset != 0:
                    plot_title = "offset: {:6} mbar".format(self.plot_offset)
            else:
                plot_y_label = "vac. wavelength (nm)"
                if self.plot_offset != 0:
                    plot_title = "offset: {:10} nm".format(self.plot_offset)

        self.stripchart = Stripchart(num_points=num_points, xvalues=True, offset=self.plot_offset,
                                     plot_title=plot_title, plot_pen=self.stripchart_pen,
                                     plot_symbol=self.stripchart_symbol, plot_bgr=self.stripchart_bgr,
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
        if self.value < 0:  # ignore error codes
            return
        if self.plot_freq:  # plot frequency in MHz
            self.stripchart.add_point(self.timestamp * 1e-3, 299792456e3 / self.value)
        else:  # plot wavelength in nm
            self.stripchart.add_point(self.timestamp * 1e-3, self.value)

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
        if not self.not_a_wavelength:
            layout.addWidget(freq_checkbox)
        widget = QWidget()
        widget.setLayout(layout)
        self.v_splitter.addWidget(widget)

    def _update_lcd(self):
        if self.value > 0:
            value = 299792456e-3 / self.value if self.lcd_freq else self.value
            # next line is to keep number of decimals constant (i.e. display trailing zeros)
            self.lcd.display("{{:.0{}f}}".format(self.lcd_ndigits-len(str(int(value)))).format(value))
        elif self.value == -2:  # bad signal
            self.lcd.display("b")
        elif self.value == -3:  # underexposed
            self.lcd.display("u")
        elif self.value == -4:  # overexposed
            self.lcd.display("o")
        else:
            self.lcd.display(self.value)

    def _init_callback(self):
        if self._enable_lcd:
            self._update_lcd()
        if self._enable_interferograms:
            self.interferograms.update_data()

    def _new_value_callback(self):
        if self._enable_stripchart:
            self._add_stripchart_point()
        if self._enable_lcd:
            self._update_lcd()
        if self._enable_interferograms:
            self.interferograms.update_data()

    def run(self):
        self.window.show()
        self._loop.run_forever()
        if self._subscriber is not None:
            self._loop.run_until_complete(self._subscriber.close())


def get_argparser():
    parser = ArgumentParser(description="Wavemeter channel monitor")
    group = parser.add_argument_group("network arguments")
    group.add_argument("-s", "--server", default="::1", help="address of the host running the wavemeter (publisher)"
                                                             " server (default: ::1 (localhost))")
    group.add_argument("-p", "--port", type=int, default=3281, help="wavemeter server publisher port (default: 3281)")
    group.add_argument("-c", "--channel", default="1", help="channel (default: 1, other options: T, p)")

    group = parser.add_argument_group("window options")
    group.add_argument("--title", default=None, help="window title")
    group.add_argument("--width", type=int, default=611, help="window width in pixels (default:611 (minimum if all"
                                                              " elements are enabled))")
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
    group.add_argument("--plot_color", default="k", help="color of the plot trace (default: k, other examples are r, g,"
                                                         " b, #00ff00 - see pyqtgraph.mkColor documentation"
                                                         " for more info)")
    group.add_argument("--plot_width", type=int, default=3, help="width of the plot trace (default: 3)")
    group.add_argument("--plot_symbol", default="", help="symbol for the plot points (default: None, examples are o, s,"
                                                         " t, + - see pyqtgrqph documentation for details)")
    group.add_argument("--plot_bgr", default="w", help="plot background color (default: w)")

    group = parser.add_argument_group("number display options")
    parser.set_defaults(no_lcd=False, lcd_freq=False)
    group.add_argument("--no_number", dest="no_lcd", action="store_true", help="disable number display")
    group.add_argument("--number_height", type=int, default=160, help="number height in pixels (default: 160)")
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

    group.add_argument("--rpc_host", default="", help="address of the host running the wavemeter RPC server "
                                                      "(default: same as publisher server)")
    group.add_argument("--rpc_port", type=int, default=3280, help="wavemeter server RPC port (default: 3280)")
    group.add_argument("--rpc_target", default="wavemeter_server",
                       help="wavemeter server RPC target (default: \"wavemeter_server\")")

    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)

    try:
        channel = "ch{}".format(int(args.channel))
    except ValueError:
        if args.channel in ["T", "p"]:
            channel = args.channel
        else:
            raise Exception("Invalid channel: {}".format(args.channel))

    rpc_host = args.server if args.rpc_host == "" else args.rpc_host

    plot_symbol = args.plot_symbol if args.plot_symbol != "" else None
    wcm = WavemeterChannelMonitor(host=args.server, channel=channel, port=args.port, rpc_host=rpc_host,
                                  rpc_port=args.rpc_port, rpc_target=args.rpc_target, title=args.title,
                                  window_width=args.width, window_x=args.x, window_y=args.y,
                                  enable_stripchart=not args.no_plot, stripchart_height=args.plot_height,
                                  stripchart_num_points=args.plot_numpoints, plot_freq=not args.plot_wl,
                                  plot_offset=args.plot_offset,
                                  plot_pen=pyqtgraph.mkPen(color=args.plot_color, width=args.plot_width),
                                  plot_symbol=plot_symbol, plot_bgr=args.plot_bgr,
                                  enable_lcd=not args.no_lcd, lcd_height=args.number_height,
                                  lcd_ndigits=args.number_digits, lcd_freq=args.lcd_freq,
                                  enable_interferograms=args.interferograms,
                                  interferogram_update_interval=args.iplot_interval,
                                  show_interferogram_update_control=args.if_update_control,
                                  if0_pen=pyqtgraph.mkPen(color=args.iplot_color, width=args.iplot_width),
                                  if1_pen=pyqtgraph.mkPen(color=args.iplot_color2, width=args.iplot_width2),
                                  interferogram_bgr=args.iplot_bgr, interferogram_height=args.iplot_height,
                                  interferogram_exposure_control=args.if_exp_control)
    wcm.run()


if __name__ == "__main__":
    main()
