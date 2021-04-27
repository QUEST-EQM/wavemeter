from sipyco.pc_rpc import Client
from sipyco.common_args import verbosity_args, init_logger_from_args

from hf_wavemeter.client import WavemeterClient

from typing import List, Any

from argparse import ArgumentParser

import asyncio

import logging
logger = logging.getLogger(__name__)


class WavemeterArtiqUpdater:
    """
    Simple loop to push new values from the wavemeter server to ARTIQ datasets. Subscribes to a list of channels and
    appends their name to a common prefix to determine the target dataset.

    :param channels: list of channels to handle entries can be of the form <n>, 'ch<n>', 'T', or 'p'
    :param host_artiq: host running the ARTIQ master
    :param port_artiq: port of the ARTIQ master's RPC server
    :param host_wavemeter_pub: host of the wavemeter publisher
    :param port_wavemeter_pub: port of the wavemeter publisher
    :param dataset_name_prefix: prefix for the target dataset names
    :param event_loop: asyncio event loop for the subscribers (defaults to asyncio.get_event_loop())
    """
    def __init__(self, channels: List[Any], host_artiq: str = "::1", port_artiq: int = 3251,
                 host_wavemeter_pub: str = "::1", port_wavemeter_pub: int = 3281,
                 dataset_name_prefix: str = "wavemeter.", event_loop: Any = None):

        self._rpc_client = None
        self._loop = asyncio.get_event_loop() if event_loop is None else event_loop

        self.channels = []
        for ch in channels:
            try:  # accept integer (or string lacking the "ch" prefix) as channel argument
                self.channels.append("ch{}".format(int(ch)))
            except ValueError:
                self.channels.append(ch)

        self.channels = list(set(self.channels))  # remove duplicates

        self.host_artiq = host_artiq
        self.port_artiq = port_artiq
        self.host_wavemeter_pub = host_wavemeter_pub
        self.port_wavemeter_pub = port_wavemeter_pub

        self._wavemeter_clients = []

        self.dataset_name_prefix = dataset_name_prefix

    def run(self):
        self._rpc_client = Client(self.host_artiq, self.port_artiq, "master_dataset_db")

        def callback_factory(client, dataset):
            def callback():
                self._rpc_client.set(dataset, client.value)
            return callback

        for channel in self.channels:
            client = WavemeterClient(channel=channel, host=self.host_wavemeter_pub, port=self.port_wavemeter_pub,
                                     event_loop=self._loop)

            client._new_value_callback = callback_factory(client, self.dataset_name_prefix + channel)

            self._wavemeter_clients.append(client)

        try:
            self._loop.run_forever()
        finally:
            self._rpc_client.close_rpc()
            for cl in self._wavemeter_clients:
                cl.close_subscriber()


def get_argparser():
    parser = ArgumentParser(description="Wavemeter ARTIQ dataset updater")
    group = parser.add_argument_group("network arguments")
    group.add_argument("-w", "--wavemeter_server", default="::1", help="address of the host running the wavemeter"
                                                                       " server (default: ::1 (localhost))")
    group.add_argument("--wavemeter_port", type=int, default=3281, help="wavemeter server publisher port"
                                                                        " (default: 3281)")
    group.add_argument("-a", "--artiq_server", default="::1", help="address of the host running the artiq master"
                                                                   " (default: ::1 (localhost))")
    group.add_argument("--artiq_port", type=int, default=3251, help="artiq master RPC server port (default: 3251)")
    group = parser.add_argument_group("source and target")
    group.add_argument("-c", "--channel", nargs="*", default=[i for i in range(1, 9)],
                       help="list of channels to read out (channel numbers, and/or T, p; default: 1 2 3 4 5 6 7 8)")
    group.add_argument("--dataset", default="wavemeter.", help="target dataset prefix (default: 'wavemeter.')")

    verbosity_args(parser)
    return parser


def main():
    args = get_argparser().parse_args()
    init_logger_from_args(args)
    channels = []
    for ch in args.channel:
        try:
            channels.append(int(ch))
        except ValueError:
            if ch in ["T", "p"]:
                channels.append(ch)
    wau = WavemeterArtiqUpdater(channels=channels, host_artiq=args.artiq_server, port_artiq=args.artiq_port,
                                host_wavemeter_pub=args.wavemeter_server, port_wavemeter_pub=args.wavemeter_port,
                                dataset_name_prefix=args.dataset)
    wau.run()


if __name__ == "__main__":
    main()
