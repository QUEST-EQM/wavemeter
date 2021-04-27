from typing import Optional

from hf_wavemeter.client import WavemeterPIClient, wavemeter_pi_client_args
try:
    from toptica.lasersdk.asyncio.dlcpro.v2_2_0 import DLCpro, NetworkConnection
except ImportError:
    raise Exception("Cannot import Toptica laser SDK (v2.2.0). Install, e.g. using \"pip install toptica_lasersdk\"")

from sipyco.common_args import bind_address_from_args, verbosity_args, init_logger_from_args
from sipyco.sync_struct import Publisher
from sipyco.pc_rpc import simple_server_loop

from argparse import ArgumentParser

import asyncio

import logging
logger = logging.getLogger(__name__)


class WavemeterPIClientTopticaEthernetPZT(WavemeterPIClient):
    """
    Implements a PI laser lock using the ethernet connection to a Toptica DLC controller to control the PZT voltage
    of an ECDL. The aux output controls the laser diode current.

    CAUTION: Please use this responsibly. Some safe guards are available (limits to PZT and current values can be set in
    here and there are probably more checks in Toptica's software), but please always be aware that you are directly
    controlling the laser diode current and PZT voltage from here.

    :param dlc_address: network address (IP or host name) of the Toptica DLC
    :param laser_id: laser ID (1 or 2)
    :param output_offset: center voltage for the PZT (defaults to the value set at startup)
    :param output_span: range over which the PZT voltage is varied around the offset
    :param current_center: center value for the LD current (aux output, defaults to the value set at startup)
    :param current_span: range over which the LD current can be varied
    """

    def __init__(self, dlc_address: str = "", laser_id: int = 1, output_offset: Optional[float] = None,
                 output_span: float = 10., warning_margin: float = 0.05, current_center: Optional[float] = None,
                 current_span: float = 0., startup_locked: bool = False, **kwargs):
        super().__init__(startup_locked=False, **kwargs)

        self.dlc = DLCpro(NetworkConnection(dlc_address))
        self._event_loop.run_until_complete(self.dlc.open())
        if laser_id == 1:
            self.pc_module = self.dlc.laser1.dl.pc
            self.cc_module = self.dlc.laser1.dl.cc
        elif laser_id == 2:
            self.pc_module = self.dlc.laser2.dl.pc
            self.cc_module = self.dlc.laser2.dl.cc
        else:
            raise Exception("Laser ID must be 1 or 2")

        self.hardware_min = self._event_loop.run_until_complete(self.pc_module.voltage_min.get())
        self.hardware_max = self._event_loop.run_until_complete(self.pc_module.voltage_max.get())

        self._set("output", self._event_loop.run_until_complete(self.pc_module.voltage_set.get()))
        output_offset = output_offset if output_offset is not None else self.get("output")
        self._set("aux_output", self._event_loop.run_until_complete(self.cc_module.current_set.get()))
        current_center = current_center if current_center is not None else self.get("aux_output")

        if output_offset > self.hardware_max or output_offset < self.hardware_min:
            raise Exception("Offset voltage must be within hardware limits")

        output_min = output_offset - 0.5 * output_span
        output_max = output_offset + 0.5 * output_span
        self.output_min = output_min if output_min > self.hardware_min else self.hardware_min
        self.output_max = output_max if output_max < self.hardware_max else self.hardware_max
        self.output_high_alert = output_max - warning_margin * (output_max - output_min)
        self.output_low_alert = output_min + warning_margin * (output_max - output_min)

        self._set("output_offset", output_offset)

        self.aux_output_min = current_center - 0.5 * current_span
        self.aux_output_max = current_center + 0.5 * current_span

        if startup_locked:
            self.relock()

    def close_dlc_connection(self):
        self.dlc.close()

    async def _set_output(self, output_val: float) -> float:
        logger.debug("setting output to {} V".format(output_val))
        await self.pc_module.voltage_set.set(output_val)
        return output_val  # trusts that limits are set appropriately (enforced by parent)

    def get_output_unit(self):
        return "V"

    async def _set_aux_output(self, output_val: float) -> float:
        await self.cc_module.current_set.set(output_val)
        return await self.cc_module.current_set.get()

    def get_aux_output_unit(self):
        return "mA"

    def get_aux_output_name(self):
        return "Laser diode current"


class WavemeterPIClientTopticaEthernetCurrent(WavemeterPIClient):
    """
    Implements a PI laser lock using the ethernet connection to a Toptica DLC controller to control the laser diode
    current.

    CAUTION: Please use this responsibly. Some safe guards are available (limits to the laser diode current values can
    be set in here and there are probably more checks in Toptica's software), but please always be aware that you are
    directly controlling the laser diode current from here.

    :param dlc_address: network address (IP or host name) of the Toptica DLC
    :param laser_id: laser ID (1 or 2)
    :param output_offset: center laser diode current (defaults to the value set at startup)
    :param output_span: range over which the PZT voltage is varied around the offset
    """

    def __init__(self, dlc_address: str = "", laser_id: int = 1, output_offset: Optional[float] = None,
                 output_span: float = 10., warning_margin: float = 0.05, startup_locked: bool = False, **kwargs):
        super().__init__(startup_locked=False, **kwargs)

        self.dlc = DLCpro(NetworkConnection(dlc_address))
        self._event_loop.run_until_complete(self.dlc.open())
        if laser_id == 1:
            self.cc_module = self.dlc.laser1.dl.cc
        elif laser_id == 2:
            self.cc_module = self.dlc.laser2.dl.cc
        else:
            raise Exception("Laser ID must be 1 or 2")

        self._set("output", self._event_loop.run_until_complete(self.cc_module.current_set.get()))
        output_offset = output_offset if output_offset is not None else self.get("output")

        output_min = output_offset - 0.5 * output_span
        output_max = output_offset + 0.5 * output_span
        self.output_min = output_min
        self.output_max = output_max
        self.output_high_alert = output_max - warning_margin * (output_max - output_min)
        self.output_low_alert = output_min + warning_margin * (output_max - output_min)

        self._set("output_offset", output_offset)

        if startup_locked:
            self.relock()

    def close_dlc_connection(self):
        self.dlc.close()

    async def _set_output(self, output_val: float) -> float:
        logger.debug("setting output to {} mA".format(output_val))
        await self.cc_module.current_set.set(output_val)
        return output_val  # trusts that limits are set appropriately (enforced by parent)

    def get_output_unit(self):
        return "mA"


def get_argparser():
    parser = ArgumentParser(description="PI lock client using ethernet communication with a Toptica DLC controller")
    wavemeter_pi_client_args(parser)
    group = parser.add_argument_group("channel selection")
    group.add_argument("-c", "--channel", default="ch1", help="wavemeter channel to read out (default: \"ch1\")")
    group = parser.add_argument_group("output configuration")
    group.add_argument("-d", "--dlc_address", default=0, help="Toptica DLC address")
    group.add_argument("-l", "--laser", type=int, default=1, help="Laser ID (1 or 2, default: 1)")
    group = parser.add_argument_group("output configuration")
    group.add_argument("--pzt_center", default=None, help="PZT center voltage in V (default: current value)")
    group.add_argument("--pzt_span", type=float, default=10., help="PZT voltage span in V (default: 10)")
    group.add_argument("--current_center", type=float, default=None,
                       help="center laser diode current in mA (default: current value)")
    # current_span intentionally defaults to 0 - users should know what they are doing if they adjust it
    group.add_argument("--current_span", type=float, default=0., help="laser diode current span in mA (default: 0)")
    parser.set_defaults(lock_current=False)
    group.add_argument("--lock_current", dest="lock_current", action="store_true",
                       help="lock using the laser diode current (default: lock via PZT voltage, make LD current "
                            "available as auxiliary output")
    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)
    if args.lock_current:
        client = WavemeterPIClientTopticaEthernetCurrent(host=args.server, port=args.port, channel=args.channel,
                                                         dlc_address=args.dlc_address, laser_id=args.laser,
                                                         output_offset=args.current_center,
                                                         output_span=args.current_span,
                                                         setpoint=args.setpoint, cp=args.cp, ci=args.ci,
                                                         integrator_timeout=args.integrator_timeout,
                                                         integrator_cutoff=args.integrator_cutoff,
                                                         output_sensitivity=args.output_sensitivity,
                                                         startup_locked=args.startup_locked)
    else:
        client = WavemeterPIClientTopticaEthernetPZT(host=args.server, port=args.port, channel=args.channel,
                                                     dlc_address=args.dlc_address, laser_id=args.laser,
                                                     output_offset=args.pzt_center, output_span=args.pzt_span,
                                                     setpoint=args.setpoint, cp=args.cp, ci=args.ci,
                                                     integrator_timeout=args.integrator_timeout,
                                                     integrator_cutoff=args.integrator_cutoff,
                                                     output_sensitivity=args.output_sensitivity,
                                                     current_center=args.current_center, current_span=args.current_span,
                                                     startup_locked=args.startup_locked)

    pub = Publisher({"lock_client_{}".format(args.channel): client.status_notifier})

    asyncio.get_event_loop().run_until_complete(pub.start(bind_address_from_args(args), args.port_pub))
    simple_server_loop({"lock_client_{}".format(args.channel): client},
                       bind_address_from_args(args), args.port_rpc)
    client.close_subscriber()
    client.close_dlc_connection()


if __name__ == "__main__":
    main()
