import pyqtgraph
import numpy as np

from typing import Any


class Stripchart(pyqtgraph.PlotWidget):
    """
    Widget to display the latest history of a continuous stream of data. Both 2D data (e.g. if timestamps are available)
    and 1D (plotted against a static x axis) are supported.

    :param num_points: the number of points to display before data is discarded
    :param xvalues: True for 2D, False for 1D input
    :param plot_pen: pen specification (see pyqtplot documentation)
    :param plot_symbol: symbol specification (see pyqtplot documentation)
    :param plot_title: title string (optional)
    :param plot_bgr: background color
    :param plot_bgr_alarm: background color while the `alarm` flag is set
    :param x_label: x axis label
    :param y_label: y axis label
    :param x_grid: displax x grid lines
    :param y_grid: displax y grid lines
    """
    def __init__(self, num_points: int = 100, xvalues: bool = False,
                 plot_pen: Any = pyqtgraph.mkPen(color="k", width=3), plot_symbol: Any = None,
                 plot_title: str = "", plot_bgr: str = "w", plot_bgr_alarm: str = "r", offset: float = 0.,
                 x_label: str = "", y_label: str = "", x_grid: bool = False, y_grid: bool = False, **kwargs):
        pyqtgraph.PlotWidget.__init__(self, background=plot_bgr, title=plot_title, **kwargs)

        self.plot_bgr = plot_bgr
        self.plot_bgr_alarm = plot_bgr_alarm
        self.alarm = False

        self.plot_pen = plot_pen
        self.plot_symbol = plot_symbol
        self.num_points = num_points
        self.xvalues = xvalues
        self.x_array = np.zeros(self.num_points)
        self.y_array = np.zeros(self.num_points)
        self.offset = offset
        self.startindex = 0.
        self.clear_flag = True
        self.plotItem.setLabels(bottom=x_label, left=y_label)
        self.plotItem.showGrid(x_grid, y_grid, 1.)

    def clear_data(self):
        """
        After a call, the next data point will clear the history.
        """
        self.clear_flag = True

    def add_point(self, v1: float, v2: float = np.nan):
        """
        Add data to the plot.

        :param v1: y value in 1D mode (xvalues == False), x value in 2D mode
        :param v2: ignored in 1D mode, y value in 2D mode
        """
        if self.startindex >= self.num_points:
            self.startindex = 0
        if self.xvalues:
            if self.clear_flag:
                self.x_array = np.array([v1 for _ in range(self.num_points)])
                self.y_array = np.array([v2 - self.offset for _ in range(self.num_points)])
                self.startindex = 0
                self.clear_flag = False
            else:
                self.startindex += 1
                self.x_array[self.startindex - 1] = v1
                self.y_array[self.startindex - 1] = v2 - self.offset
        else:  # interpret v1 as y value
            if self.clear_flag:
                self.x_array = np.array(range(self.num_points))
                self.y_array = np.array([v1 - self.offset for _ in range(self.num_points)])
                self.startindex = 0
                self.clear_flag = False
            else:
                self.startindex += 1
                self.y_array[self.startindex - 1] = v1 - self.offset

        self.data_changed()

    def set_alarm(self, alarm: bool = True):
        """
        Set or clear the `alarm` flag.

        :param alarm: boolean to indicate alarm status
        """
        self.alarm = alarm
        self.data_changed()

    def data_changed(self):
        """
        Update the display.
        """
        self.setBackground(self.plot_bgr if not self.alarm else self.plot_bgr_alarm)

        x = np.roll(self.x_array, - self.startindex) if self.xvalues else self.x_array
        y = np.roll(self.y_array, - self.startindex)

        # stupid workaround for a stupid bug (traces are not shown if they contain a single NaN)
        y_not_nan_mask = ~np.isnan(y)
        x = x[y_not_nan_mask]
        y = y[y_not_nan_mask]

        if not len(y) or len(y) != len(x):
            return

        self.clear()
        self.plot(x, y, pen=self.plot_pen, symbol=self.plot_symbol)


class Stripchart2Traces(Stripchart):
    """
    Same as Stripchart, but with the option to add a 2nd y value for each x.

    :param plot_pen2: pen specification for the 2nd trace
    :param plot_symbol2: symbol specification for the 2nd trace
    :param offset2: offset for the 2nd trace (set to None to use the same as the 1st trace)
    """
    def __init__(self, num_points: int = 100, xvalues: bool = False,
                 plot_pen: Any = pyqtgraph.mkPen(color="k", width=3), plot_symbol: Any = None,
                 plot_pen2: Any = pyqtgraph.mkPen(color="g", width=2), plot_symbol2: Any = None, plot_title: str = "",
                 plot_bgr: str = "w", plot_bgr_alarm: str = "r", offset: float = 0., offset2: float = None, **kwargs):
        Stripchart.__init__(self, num_points, xvalues, plot_pen, plot_symbol, plot_title, plot_bgr, plot_bgr_alarm,
                            offset, **kwargs)
        self.plot_pen2 = plot_pen2
        self.plot_symbol2 = plot_symbol2
        self.offset2 = offset if offset2 is None else offset2
        self.y2_array = np.zeros(self.num_points)

    def add_point(self, v1: float, v2: float = np.nan, v3: float = np.nan):
        """
        Add data to the plot.

        :param v1: y1 value in 1D mode (xvalues == False), x value in 2D mode
        :param v2: y2 value in 1D mode, y1 value in 2D mode
        :param v3: ignored in 1D mode, y2 value in 2D mode

        """
        if self.startindex >= self.num_points:
            self.startindex = 0
        if self.xvalues:
            if self.clear_flag:
                self.x_array = np.array([v1 for _ in range(self.num_points)])
                self.y_array = np.array([v2 - self.offset for _ in range(self.num_points)])
                self.y2_array = np.array([v3 - self.offset2 for _ in range(self.num_points)])
                self.startindex = 0
                self.clear_flag = False
            else:
                self.startindex += 1
                self.x_array[self.startindex - 1] = v1
                self.y_array[self.startindex - 1] = v2 - self.offset
                self.y2_array[self.startindex - 1] = v3 - self.offset2
        else:  # interpret v1 as y value, v2 as y2 value
            if self.clear_flag:
                self.x_array = np.array(range(self.num_points))
                self.y_array = np.array([v1 - self.offset for _ in range(self.num_points)])
                self.y2_array = np.array([v2 - self.offset2 for _ in range(self.num_points)])
                self.startindex = 0
                self.clear_flag = False
            else:
                self.startindex += 1
                self.y_array[self.startindex - 1] = v1 - self.offset
                self.y2_array[self.startindex - 1] = v2 - self.offset2

        self.data_changed()

    def data_changed(self):
        """
        Update the display.
        """
        self.setBackground(self.plot_bgr if not self.alarm else self.plot_bgr_alarm)

        x = np.roll(self.x_array, - self.startindex) if self.xvalues else self.x_array
        x2 = np.array(x)
        y = np.roll(self.y_array, - self.startindex)
        y2 = np.roll(self.y2_array, - self.startindex)

        # stupid workaround for a stupid bug (traces are not shown if they contain a single NaN)
        y_not_nan_mask = ~np.isnan(y)
        x = x[y_not_nan_mask]
        y = y[y_not_nan_mask]
        y2_not_nan_mask = ~np.isnan(y2)
        x2 = x2[y2_not_nan_mask]
        y2 = y2[y2_not_nan_mask]

        if not len(y) or len(y) != len(x):
            return

        self.clear()
        self.plot(x, y, pen=self.plot_pen, symbol=self.plot_symbol)

        if not len(y2) or len(y2) != len(x2):
            return

        self.plot(x2, y2, pen=self.plot_pen2, symbol=self.plot_symbol2)
