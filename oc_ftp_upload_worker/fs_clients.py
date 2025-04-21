#!/usr/bin/env python3
"""Basic clients for external systems"""
import fs.errors
import pysvn
from oc_pyfs.SvnFS import SvnFS
from oc_pyfs.NexusFS import NexusFS
from oc_cdtapi.NexusAPI import NexusAPI
from fs.ftpfs import FTPFS
import urllib.parse
from smtplib import SMTP

from oc_ftp_upload_worker.upload_errors import EnvironmentSetupError


def get_svn_fs_client(url, user, password):
    """
    Construct and return SVN FS client
    :param str url:
    :param str user:
    :param str password:
    """

    if not all([url, user, password]):
        raise ValueError(f"Some credentials not set for SVN: url=[{bool(url)}], user=[{bool(user)}], password=[{bool(password)}]")
    
    class OneAttemptLogin:
        """
        pysvn goes into infinite loop on login failure. This class allows only one login attempt.
        """

        def __init__(self):
            self.__attempt_tried = False
            
        def __call__(self, x, y, z):
            # return value: retcode, username, password, credentials caching
            if not self.__attempt_tried:
                self.__attempt_tried = True
                return True, user, password, False
            else:
                return False, "xx", "xx", False

    _client = pysvn.Client()
    _client.callback_get_login = OneAttemptLogin()
    _client.callback_ssl_server_trust_prompt=lambda trust_dict: (True, trust_dict["failures"], True)
    _client.set_auth_cache(False)
    _client.set_store_passwords(False)
    _client.set_default_username(None)
    _client.set_default_password(None)
    _client.set_interactive(False)

    return SvnFS(url, _client)

def get_ftp_fs_client(url, user, password):
    """
    Return fs-like client for FTP
    :param str url:
    """
    if not all([url, user, password]):
        raise ValueError(f"Some credentials not set for FTP: url=[{bool(url)}], user=[{bool(user)}], password=[{bool(password)}]")

    _url = urllib.parse.urlparse(url)

    try:
        ftp_fs = FTPFS(user=user, passwd=password, host=_url.hostname, port=_url.port)
        ftp_fs.ftp #Checking if the ftp_fs is using correct credentials

        return ftp_fs
    except fs.errors.PermissionDenied as _pd:
        raise EnvironmentSetupError("FTP login credentials incorrect")

def get_mvn_fs_client(url, user, password, work_fs, **kwargs):
    """
    Return fs-like client for MVN connection
    """
    _client = NexusAPI(root=url, user=user, auth=password, **kwargs)
    return NexusFS(_client, work_fs=work_fs)

def get_smtp_client(url, user, password):
    """
    Return SMTP connection
    """
    
    if not url:
        raise ValueError("SMTP_URL not set")

    _url = urllib.parse.urlparse(url)

    client = SMTP(host=_url.hostname, port=_url.port)

    if user:
        if not password:
            ValueError("SMTP_USER provided but no SMTP_PASSWORD given")

        client.login(user, password)
    return client
    
