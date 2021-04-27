from hf_wavemeter.client import WavemeterPIClient, wavemeter_pi_client_args
from hf_wavemeter.hw.Meilhaus_RedLab_analog_out import RedLabAnalogOut

from sipyco.common_args import bind_address_from_args, verbosity_args, init_logger_from_args
from sipyco.sync_struct import Publisher
from sipyco.pc_rpc import simple_server_loop

from argparse import ArgumentParser

import asyncio

import logging
logger = logging.getLogger(__name__)


class WavemeterPIClientRedLab(WavemeterPIClient):
    """
    Implements a PI laser lock using a Meilhaus RedLab analog out channel.

    :param board_num: board number
    :param channel_id: channel ID
    """

    def __init__(self, board_num: int, channel_id: int, output_min: float = -10., output_max: float = 10., **kwargs):
        self.output = RedLabAnalogOut(board_num, channel_id)
        output_min = output_min if output_min > self.output.v_min else self.output.v_min
        output_max = output_max if output_max < self.output.v_max else self.output.v_max
        super().__init__(output_min=output_min, output_max=output_max, **kwargs)

    async def _set_output(self, output_val: float):
        logger.debug("setting output to {} V".format(output_val))
        self.output.set(output_val)
        return output_val  # trusts that limits are set appropriately (enforced by parent)

    def get_output_unit(self):
        return "V"


class WavemeterPIClientRedLabPlusAuxOut(WavemeterPIClientRedLab):
    """
    Implements a PI laser lock using a Meilhaus RedLab analog out channel.
    This one includes an aux output which enables remote access to another channel.
    Used, e.g. for tweaking diode laser currents remotely.

    :param board_num_aux: board number of the auxiliary output
    :param channel_id_aux: channel ID of the auxiliary output
    """

    def __init__(self, board_num: int, channel_id: int, board_num_aux: int, channel_id_aux: int, **kwargs):
        self.aux_output = RedLabAnalogOut(board_num_aux, channel_id_aux)
        super().__init__(board_num, channel_id, **kwargs)

    async def set_aux_output(self, voltage: float):
        """
        Change the voltage of the auxiliary analog out channel.

        :param voltage: output voltage
        """
        logger.debug("setting aux output to {} V".format(voltage))
        self.aux_output.set(voltage)
        return voltage  # trusts that limits are set appropriately (enforced by parent)


def get_argparser():
    parser = ArgumentParser(description="PI lock client with a Meilhaus RedLab analog out")
    wavemeter_pi_client_args(parser)
    group = parser.add_argument_group("channel selection")
    group.add_argument("-c", "--channel", default="ch1", help="wavemeter channel to read out (default: \"ch1\")")
    group = parser.add_argument_group("output configuration")
    group.add_argument("-b", "--board", default=0, help="board number")
    group.add_argument("-i", "--id", default=0, help="channel id")
    parser.set_defaults(aux_output=False)
    group.add_argument("--aux-output", dest="aux_output", action="store_true", help="enable additional output "
                                                                                    "(independent of lock, but can be "
                                                                                    "manually updated, e.g. to tweak "
                                                                                    "the current)")
    group.add_argument("-b2", "--board2", default=0, help="board number (aux out)")
    group.add_argument("-i2", "--id2", default=0, help="channel id (aux out)")

    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)
    if args.aux_output:
        client = WavemeterPIClientRedLabPlusAuxOut(host=args.server, port=args.port, channel=args.channel,
                                                   board_num=args.board, channel_id=args.id,
                                                   board_num_aux=args.board2, channel_id_aux=args.id2,
                                                   setpoint=args.setpoint, cp=args.cp, ci=args.ci,
                                                   integrator_timeout=args.integrator_timeout,
                                                   integrator_cutoff=args.integrator_cutoff,
                                                   output_sensitivity=args.output_sensitivity,
                                                   output_offset=args.output_offset,
                                                   startup_locked=args.startup_locked)
    else:
        client = WavemeterPIClientRedLab(host=args.server, port=args.port, channel=args.channel, board_num=args.board,
                                         channel_id=args.id, setpoint=args.setpoint, cp=args.cp, ci=args.ci,
                                         integrator_timeout=args.integrator_timeout,
                                         integrator_cutoff=args.integrator_cutoff,
                                         output_sensitivity=args.output_sensitivity, output_offset=args.output_offset,
                                         startup_locked=args.startup_locked)
    pub = Publisher({"lock_client_{}".format(args.channel): client.status_notifier})

    asyncio.get_event_loop().run_until_complete(pub.start(bind_address_from_args(args), args.port_pub))
    simple_server_loop({"lock_client_{}".format(args.channel): client},
                       bind_address_from_args(args), args.port_rpc)
    client.close_subscriber()


if __name__ == "__main__":
    main()
