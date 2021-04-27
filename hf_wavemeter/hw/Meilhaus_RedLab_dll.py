from typing import Any, List
from ctypes import (
    WinDLL,
    byref,
    c_int,
    c_ushort,
    POINTER,
    CFUNCTYPE,
)
import os


def null_function():
    pass


def bind(lib: WinDLL, func: str,
         argtypes: List[Any] = None, restype: Any = None) -> CFUNCTYPE:
    _func = getattr(lib, func, null_function)
    _func.argtypes = argtypes
    _func.restype = restype

    return _func


class RedLabDLL:
    def __init__(self):
        # load dll (Not sure why I manually have to go through the PATH, but for some reason it's not found otherwise)
        self.lib = None
        for p in os.environ["PATH"].split(";"):
            try:
                self.lib = WinDLL(os.path.join(p, "cbw64.dll"))
                break
            except FileNotFoundError:
                pass

        if self.lib is None:
            raise Exception("Could not load \"cbw64.dll\". Please make sure the Meilhaus SDK is installed and this"
                            " file is in the PATH.")

        # binds
        self.cbGetConfig = bind(self.lib, "cbGetConfig", [c_int, c_int, c_int, c_int, POINTER(c_int)], c_int)
        self.cbSetConfig = bind(self.lib, "cbSetConfig", [c_int, c_int, c_int, c_int, c_int], c_int)
        self.cbAOut = bind(self.lib, "cbAOut", [c_int, c_int, c_int, c_ushort], c_int)

        # constants from the header
        self.BOARDINFO = 2
        self.BIDACRANGE = 114
        self.BIDACRES = 292

        # DAC RANGE return values
        self.dac_range = {
            20: (-60., 60.),
            23: (-30., 30.),
            15: (-20., 20.),
            21: (-15., 15.),
            1: (-10., 10.),
            0: (-5., 5.),
            16: (-4., 4.),
            2: (-2.5, 2.5),
            14: (-2., 2.),
            3: (-1.25, 1.25),
            4: (-1., 1.),
            5: (-0.625, 0.625),
            6: (-0.5, 0.5),
            12: (-0.25, 0.25),
            13: (-0.2, 0.2),
            7: (-0.1, 0.1),
            8: (-0.05, 0.05),
            9: (-0.01, 0.01),
            10: (-0.005, 0.005),
            11: (-1.67, 1.67),
            17: (-0.312, 0.312),
            18: (-0.156, 0.156),
            22: (-0.125, 0.125),
            19: (-0.078, 0.078),
            100: (0., 10.),
            101: (0., 5.),
            114: (0., 4.),
            102: (0., 2.5),
            103: (0., 2.),
            109: (0., 1.67),
            104: (0., 1.25),
            105: (0., 1.),
            110: (0., 0.5),
            111: (0., 0.25),
            112: (0., 0.2),
            106: (0., 0.1),
            113: (0., 0.05),
            108: (0., 0.02),
            107: (0., 0.01)
        }

    def cb_get_config(self, info_type: int, board_num: int, dev_num: int, config_item: int) -> int:
        returnvalue = c_int()
        err = self.cbGetConfig(info_type, board_num, dev_num, config_item, byref(returnvalue))
        if not err == 0:
            raise Exception(err)
        return returnvalue.value
