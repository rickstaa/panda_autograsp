# -*- coding: utf-8 -*-
"""
Setup of `panda_autograsp` Python codebase. This setup file was based upon the gqcnn setup.py file.

Author
------
Rick Staa
"""

## Imports
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import os
from setuptools import setup, find_packages
from setuptools.command.develop import develop
from setuptools.command.install import install
import subprocess
import sys
import re
import shutil

## General setup.py parameters ##
TF_MAX_VERSION = "1.13.1"

## Package requirements ##
requirements = []

## Set up logger. ##
logging.basicConfig()  # Configure the root logger.
logger = logging.getLogger("setup.py")
logger.setLevel(logging.INFO)

## Append submodule requirements ##

# Find git submodules
sub_mod_bash_command = "git config --file " + \
   os.getcwd()+"/.gitmodules --get-regexp path | awk '{ print $2 }'"
bash_output = subprocess.check_output(['bash', '-c', sub_mod_bash_command])
sub_mods = [x for x in bash_output.decode("utf-8").split("\n") if x != '']

# Extend requirements list with submodule requirements
for sub_mod in sub_mods:
   submod_setup_path = os.getcwd()+"/"+sub_mod+"/setup.py"
   if os.path.exists(submod_setup_path):

        # Generate requires.txt file for the submodule using the setuptools.egg_info module
        try:
            subprocess.call(["python",submod_setup_path,"egg_info"])

            # Open egg_info generated requires.txt file
            with open(os.getcwd()+"/gqcnn.egg-info/requires.txt") as file:

                # Read requires file up till empty line
                for line in file:
                    if line.strip() == "":

                        ## Try to remove tree; if failed show an error using try...except on screen
                        try:
                            shutil.rmtree(os.getcwd()+"/gqcnn.egg-info")
                        except OSError as e:
                            print ("Error: %s - %s." % (e.filename, e.strerror))
                        break
                    requirements.append(line.strip()) # Append submodule requirement to package requirements
        except Exception as e:
            logger.warning("Submodule dependencies could not be imported. "+str(e))

## Get GPU information ##
def get_tf_dep():
    # Check whether or not the Nvidia driver and GPUs are available and add the
    # corresponding Tensorflow dependency.
    tf_dep = "tensorflow<={}".format(TF_MAX_VERSION)
    try:
        gpus = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=gpu_name",
             "--format=csv"]).decode().strip().split("\n")[1:]
        if len(gpus) > 0:
            tf_dep = "tensorflow-gpu<={}".format(TF_MAX_VERSION)
        else:
            no_device_msg = ("Found Nvidia device driver but no"
                             " devices...installing Tensorflow for CPU.")
            logger.warning(no_device_msg)
    except OSError:
        no_driver_msg = ("Could not find Nvidia device driver...installing"
                         " Tensorflow for CPU.")
        logger.warning(no_driver_msg)
    return tf_dep


## Create develop cmd ##
class DevelopCmd(develop):
    user_options_custom = [
        ("docker", None, "installing in Docker"),
    ]
    user_options = getattr(develop, "user_options", []) + user_options_custom

    def initialize_options(self):
        develop.initialize_options(self)

        # Initialize options.
        self.docker = False

    def finalize_options(self):
        develop.finalize_options(self)

    def run(self):
        # Install Tensorflow dependency.
        if not self.docker:
            tf_dep = get_tf_dep()
            subprocess.Popen([sys.executable, "-m", "pip", "install",
                              tf_dep]).wait()
        else:
            # If we're using Docker, this will already have been installed
            # explicitly through the correct `{cpu/gpu}_requirements.txt`;
            # there is no way to check for CUDA/GPUs at Docker build time
            # because there is no easy way to set the Nvidia runtime.
            skip_tf_msg = ("Omitting Tensorflow dependency because of Docker"
                           " installation.")
            logger.warning(skip_tf_msg)

        # Run installation.
        develop.run(self)


## Create install cmd ##
class InstallCmd(install, object):
    user_options_custom = [
        ("docker", None, "installing in Docker"),
    ]
    user_options = getattr(install, "user_options", []) + user_options_custom

    def initialize_options(self):
        install.initialize_options(self)

        # Initialize options.
        self.docker = False

    def finalize_options(self):
        install.finalize_options(self)

    def run(self):

        # Install Tensorflow dependency.
        if not self.docker:
            tf_dep = get_tf_dep()
            subprocess.Popen([sys.executable, "-m", "pip", "install",
                              tf_dep]).wait()
        else:
            # If we're using Docker, this will already have been installed
            # explicitly through the correct `{cpu/gpu}_requirements.txt`;
            # there is no way to check for CUDA/GPUs at Docker build time
            # because there is no easy way to set the Nvidia runtime.
            skip_tf_msg = ("Omitting Tensorflow dependency because of Docker"
                           " installation.")
            logger.warning(skip_tf_msg)

        # Run installation.
        install.run(self)


## Get current package version ##
__version__ = re.sub(r'[^\d.]', '', open(
    os.path.join(os.path.dirname(os.path.realpath(__file__)),
                 "panda_autograsp/version.py")).read())

## Run python setup ##
setup(
    name="panda_autograsp",
    version=__version__,
    description=(
        "Project code for the panda_autograsp automatic grasping solution."),
    author="Rick Staa",
    author_email="rick.staa@outlook.com",
    license="Rick Staa Copyright",
    url="https://github.com/rickstaa/panda_autograsp",
    keywords="robotics grasping vision deep learning franka gqcnn gpd",
    classifiers=[
        "Development Status :: 1 - Beta",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Natural Language :: English",
        "Topic :: Scientific/Engineering"
    ],
    packages=find_packages(),
    install_requires=requirements,
    extras_require={
        "docs": ["sphinx", "sphinxcontrib-napoleon", "sphinx_rtd_theme"],
    },
    cmdclass={
        "install": InstallCmd,
        "develop": DevelopCmd
    })
