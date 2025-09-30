#!/usr/bin/env python3
from setuptools import setup
import os
import glob

__version = '2.1.1'

def list_recursive(app, directory, extension="*"):
    dir_to_walk = os.path.join(app, directory)

    found = [result for (cur_dir, subdirs, files) in os.walk(dir_to_walk)
             for result in glob.glob(os.path.join(cur_dir, '*.' + extension))]

    found_in_package = list(map(lambda x: x.replace(app + os.path.sep, "", 1), found))
    return found_in_package

_spec = {
        "name": "oc-ftp-upload-worker",
        "version": __version,
        "description": "Delivery upload worker",
        "long_description": "",
        "long_description_content_type": "text/plain",
        "install_requires": [
            # pysvn shoud be installed as binary
            "oc-cdtapi",
            "oc-pyfs",
            "oc-mailer",
            "oc-delivery-apps",
            "oc-orm-initializator",
            "oc-dlinterface",
            "python-gnupg >= 0.4.1",
            "packaging",
            "pyparsing",
            "fs",
            "pyyaml",
            "oc-cdt-queue2 >= 4.0.1",
            "oc-logging",
            ],
        "python_requires": ">=3.6",
        "packages": ["oc_ftp_upload_worker"],
        "package_data": {"oc_ftp_upload_worker": list_recursive("oc_ftp_upload_worker", "resources")},}

setup (**_spec)
