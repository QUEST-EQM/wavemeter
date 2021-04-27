from setuptools import setup

setup(
    name = "hf_wavemeter",
    version = "1.3",
    description = "HighFinesse wavemeter tools",
    author = "Jonas Keller",
    author_email = "jonaskeller@gmx.de",
    packages = ["hf_wavemeter", "hf_wavemeter.gui", "hf_wavemeter.hw"],
    long_description = "",
    entry_points = {
        "console_scripts": [
            "wavemeter_server = hf_wavemeter.server:main",
            "wavemeter_remote = hf_wavemeter.remote:main",
            "wavemeter_channel_monitor = hf_wavemeter.channel_monitor:main",
            "wavemeter_pi_client_redlab = hf_wavemeter.pi_client_redlab:main",
            "wavemeter_pi_client_toptica = hf_wavemeter.pi_client_toptica:main", 
            "wavemeter_lock_remote = hf_wavemeter.lock_remote:main",
            "wavemeter_logging_client = hf_wavemeter.logging_client:main",
            "wavemeter_artiq_updater = hf_wavemeter.artiq_updater:main"
        ]
    }
)
