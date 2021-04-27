# Adapted from M-Labs' (https://m-labs.hk) former conda installation script for ARTIQ
# Choose the name of the conda environment to create
CONDA_ENV_NAME = "wavemeter"
# Choose the components for which to install the dependencies
SERVER_DEPENDENCIES = True
GUI_DEPENDENCIES = True

CONDA_PACKAGES = ["python",
                  "sipyco"]

if SERVER_DEPENDENCIES:
    CONDA_PACKAGES += ["janus"]

if GUI_DEPENDENCIES:
    CONDA_PACKAGES += ["numpy",
                       "qt",
                       "pyqt",
                       "asyncqt",
                       "pyqtgraph"]

# Set to False if you have already set up conda channels
ADD_CHANNELS = True

import os

def run(command):
    r = os.system(command)
    if r != 0:
        raise SystemExit("command '{}' returned non-zero exit status: {}".format(command, r))

if ADD_CHANNELS:
    run("conda config --prepend channels m-labs")
    run("conda config --prepend channels https://conda.m-labs.hk/artiq")
    run("conda config --prepend channels conda-forge")
    

run("conda create -y -n {CONDA_ENV_NAME}".format(CONDA_ENV_NAME=CONDA_ENV_NAME))
for package in CONDA_PACKAGES:
    run("conda install -y -n {CONDA_ENV_NAME} {package}"
        .format(CONDA_ENV_NAME=CONDA_ENV_NAME, package=package))