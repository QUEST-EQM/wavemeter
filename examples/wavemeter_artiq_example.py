import time
from artiq.experiment import *

class WavemeterExample(EnvExperiment):
    """Wavemeter example
    device_db entry:
    device_db.update({"wavemeter":
        {
        "type": "controller",
        "host": "::1",
        "port": 3280,
        "target_name": "wavemeter_server"
        }
    })
    """
    def build(self):
        self.setattr_device("wavemeter")
        
    def run(self):
        print("Ch4: {} nm".format(self.wavemeter.get_wavelength(4)))
        temperature = self.wavemeter.get_temperature()
        if temperature == -1:
            print("Temperature readout disabled. Start server with -T to enable.")
        else:
            print("T = {} deg. C".format(temperature))
        cal_time = time.time() - self.wavemeter.get_time_since_calibration()
        if cal_time > 0:
            print("Last successful calibration at {}".format(time.asctime(time.localtime(int(time.time() - self.wavemeter.get_time_since_calibration())))))
        else:
            print("No successful calibration via server since it was started.")


  
class LockControlExample(EnvExperiment):
    """Wavemeter lock control example
    
    device_db entry:
    device_db.update({"lock_client_ch1":
         {
        "type": "controller",
        "host": "::1",
        "port": 3284,
        "target_name": "lock_client_ch1"
        }
    })
    """
    def build(self):
        self.setattr_device("lock_client_ch1")
        
    def run(self):
        print("Output at {} {}".format(self.lock_client_ch1.get("output"), self.lock_client_ch1.get_output_unit()))
        if self.lock_client_ch1.get("locked"):
            print("Locked at {} nm".format(self.lock_client_ch1.get("setpoint")))
            print("Actual wavelength: {} nm".format(self.lock_client_ch1.get("latest_value")))
            self.lock_client_ch1.setpoint_step_mhz(10.)
            print("Locked at {} nm".format(self.lock_client_ch1.get("setpoint")))
            time.sleep(1.)
            print("Actual wavelength: {} nm".format(self.lock_client_ch1.get("latest_value")))
            time.sleep(5.)
            self.lock_client_ch1.setpoint_step_mhz(-10.)
            print("Locked at {} nm".format(self.lock_client_ch1.get("setpoint")))
            time.sleep(1.)
            print("Actual wavelength: {} nm".format(self.lock_client_ch1.get("latest_value")))
        else:
            print("Not locked")
            print("Actual wavelength: {} nm".format(self.lock_client_ch1.get("latest_value")))
        
        


