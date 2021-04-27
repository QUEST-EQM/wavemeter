from hf_wavemeter.pi_client_redlab import WavemeterPIClientRedLab

from sipyco.pc_rpc import simple_server_loop
from sipyco.sync_struct import Publisher
import asyncio

import logging
logger = logging.getLogger(__name__)

logger.setLevel(logging.INFO)

rpc_port = 3284
publisher_port = 3282

lockClient825 = WavemeterPIClientRedLab(channel="ch1", board_num=0, channel_id=0, output_min=-5., output_max=5.,
                                        setpoint=825.147215, cp=20., ci=0.4, output_sensitivity=-0.00205)
# relock system                                        
lockClient922 = WavemeterPIClientRedLab(channel="ch2", board_num=0, channel_id=14, output_min=-5., output_max=5.,
                                        setpoint=922.687462, cp=0., ci=-0.4, integrator_cutoff=0.00001)
# relock system (channel shared, switched externally when relock is triggered)
lockClient946 = WavemeterPIClientRedLab(channel="ch2", board_num=0, channel_id=15, output_min=-5., output_max=5.,
                                        setpoint=946.16334, cp=0., ci=-0.4, integrator_cutoff=0.00001)

lockClient410 = WavemeterPIClientRedLab(channel="ch3", board_num=0, channel_id=2, output_min=-5., output_max=5.,
                                        setpoint=410.285228, cp=1000., ci=10., output_sensitivity=-0.000338)

lockClient638 = WavemeterPIClientRedLab(channel="ch5", board_num=0, channel_id=4, output_min=-5., output_max=5.,
                                        setpoint=638.615082, cp=150., ci=1., output_sensitivity=-0.000642)

lockClient935 = WavemeterPIClientRedLab(channel="ch6", board_num=0, channel_id=6, output_min=-5., output_max=5.,
                                        setpoint=935.187424, cp=100., ci=1., output_sensitivity=-0.00215)

lockClient798 = WavemeterPIClientRedLab(channel="ch7", board_num=0, channel_id=8, output_min=-5., output_max=5.,
                                        setpoint=797.822139, cp=200., ci=2.7, output_sensitivity=-0.00113)

lockClient739 = WavemeterPIClientRedLab(channel="ch8", board_num=0, channel_id=10, output_min=-5., output_max=5.,
                                        setpoint=739.049050, cp=100., ci=2., output_sensitivity=-0.000741)


pub = Publisher({"lock825": lockClient825.status_notifier,
                 "lock922": lockClient922.status_notifier,
                 "lock946": lockClient946.status_notifier,
                 "lock410": lockClient410.status_notifier,
                 "lock638": lockClient638.status_notifier,
                 "lock935": lockClient935.status_notifier,
                 "lock798": lockClient798.status_notifier,
                 "lock739": lockClient739.status_notifier})

asyncio.get_event_loop().create_task(pub.start(None, publisher_port))

simple_server_loop({"lock825": lockClient825,
                    "lock922": lockClient922,
                    "lock946": lockClient946,
                    "lock410": lockClient410,
                    "lock638": lockClient638,
                    "lock935": lockClient935,
                    "lock798": lockClient798,
                    "lock739": lockClient739}, None, rpc_port)