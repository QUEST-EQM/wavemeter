from hf_wavemeter.hw.Meilhaus_RedLab_dll import RedLabDLL


class RedLabAnalogOut:
    def __init__(self, board_num: int, channel_id: int):
        self.board_num = board_num
        self.channel_id = channel_id

        self.rl_dll = RedLabDLL()

        self.v_range = self.rl_dll.cb_get_config(self.rl_dll.BOARDINFO, board_num, channel_id, self.rl_dll.BIDACRANGE)
        self.v_min = 0.
        self.v_max = 0.

        try:
            self.v_min, self.v_max = self.rl_dll.dac_range[self.v_range]
        except KeyError:
            raise Exception("Error determining analog out range for RedLab board {}, channel {}".format(board_num,
                                                                                                        channel_id))

        self.resolution = self.rl_dll.cb_get_config(self.rl_dll.BOARDINFO, board_num, channel_id, self.rl_dll.BIDACRES)

    def set(self, voltage: float):
        value = int((voltage - self.v_min)/(self.v_max - self.v_min)*(2**self.resolution - 1))
        err = self.rl_dll.cbAOut(self.board_num, self.channel_id, self.v_range, value)
        if not err == 0:
            raise Exception(
                "Error {} setting output voltage for RedLab board {}, DAC channel {}".format(err,
                                                                                             self.board_num,
                                                                                             self.channel_id))
