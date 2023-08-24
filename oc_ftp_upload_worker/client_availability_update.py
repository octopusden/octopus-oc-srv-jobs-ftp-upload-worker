#!/usr/bin/env python3
""" Checks if delivery can be send to client via FTP. Updates corresponding client's flag in database """

import os
from fs.errors import ResourceNotFound
import logging

import posixpath
from .fs_clients import get_svn_fs_client, get_ftp_fs_client

def update_send_availability_statuses(clients, **kwargs):
    """ Top-level wrapper for clients status update 

    :param list clients: list of clients to update, as records from DB
    :param str svn_clients_url:
    :param str svn_clients_user:
    :param str svn_cliennts_password:
    :param str ftp_url:
    :param str ftp_user:
    :param str ftp_password:
    """
    # this case we should raise an error if anything absent, so do not use '.get' method of 'kwargs'
    svn_fs = get_svn_fs_client(kwargs['svn_clients_url'], kwargs['svn_clients_user'], kwargs['svn_clients_password'])
    ftp_fs = get_ftp_fs_client(kwargs['ftp_url'], kwargs['ftp_user'], kwargs['ftp_password']) 

    for client in clients:
        can_receive_encrypted = is_client_encrypted_send_available(client, svn_fs, ftp_fs)
        update_can_receive_status(client, can_receive_encrypted)


def is_client_encrypted_send_available(client, svn_fs, ftp_fs):
    """ Checks whether target public keys are available and FTP directory exists 

    :param str client: client to check
    :param SvnFS svn_fs: FS object pointing to repository root
    :param FTPFS ftp_fs: FS object pointing to FTP root
    :return bool: representing whether client can receive encrypted deliveries """
    try:
        client_data_path = posixpath.join(client.country, client.code, "data")
        data_contents = svn_fs.listdir(client_data_path)

        if not any(name.endswith(".asc") for name in data_contents):
            logging.info(f"No *.asc files found at [{client_data_path}]")
            return False

        ftp_path = posixpath.join(client.code, "TO_BNK")

        if not ftp_fs.exists(ftp_path):
            logging.info(f"Not found in FTP: [{ftp_path}]")
            return False

        logging.info(f"Client [{client.code}] can receive encrypted deliveries")
        return True

    except ResourceNotFound:
        logging.info(f"Not found in SVN: [{client_data_path}]")
        return False


def update_can_receive_status(client, can_receive_encrypted):
    """ Sets can_receive flag based on client params and encrypted send availability.
    Reason for this method to be separated is to allow further fine receivability setup
    (e.g. based on signed send availability as well) 

    :param str client: client to update 
    :param bool can_receive_encrypted: result of availability check 
    """
    from oc_delivery_apps.dlmanager.models import FtpUploadClientOptions
    options, _c = FtpUploadClientOptions.objects.get_or_create(client=client)

    if options.should_encrypt:
        # encrypting client's status is based on previous check
        options.can_receive = can_receive_encrypted
    else:
        # signing client availability currently is not validated, so we allow send
        logging.warning(f"[{client.code}] doesn't receive encrypted deliveries, so upload is allowed")
        options.can_receive = True
    options.save()

    logging.info(f"Set [{client.code}] availability to [{options.can_receive}]")


if __name__ == "__main__":
    from argparse import ArgumentParser
    parser = ArgumentParser(description="Client availability update")
    parser.add_argument("--client", dest="client", help="Code of client to update status", required=False)
    parser.add_argument("--log-level", dest="log_level", help="Set log level", type=int, default=50)

    ### FTP arguments
    parser.add_argument("--ftp-url", dest="ftp_url", help="FTP URL",
                        default=os.getenv("FTP_URL"))
    parser.add_argument("--ftp-user", dest="ftp_user", help="FTP user",
                        default=os.getenv("FTP_USER"))
    parser.add_argument("--ftp-password", dest="ftp_password", help="FTP password",
                        default=os.getenv("FTP_PASSWORD"))

    ### SVN (subversion) arguments
    parser.add_argument("--svn-clients-url", dest="svn_clients_url", help="SVN URL for clients section",
                        default=os.getenv("SVN_CLIENTS_URL"))
    parser.add_argument("--svn-clients-user", dest="svn_clients_user", help="SVN user for clients section",
                        default=os.getenv("SVN_CLIENTS_USER"))
    parser.add_argument("--svn-clients-password", dest="svn_clients_password", help="SVN password for clients section",
                        default=os.getenv("SVN_CLIENTS_PASSWORD"))

    ### PSQL (PostGres) arguments
    parser.add_argument("--psql-url", dest="psql_url", help="PSQL URL, including schema path",
            default=os.getenv("PSQL_URL"))
    parser.add_argument("--psql-user", dest="psql_user", help="PSQL user",
            default=os.getenv("PSQL_USER"))
    parser.add_argument("--psql-password", dest="psql_password", help="PSQL password",
            default=os.getenv("PSQL_PASSWORD"))

    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)

    #Log args:
    for _k, _v in args.__dict__.items():
        _display_value = _v

        if _v and _k.endswith('password'):
            _display_value = '*'*len(_v)

        logging.info(f"{_k.upper()}:\t[{_display_value}]")


    # ORM-related stuff must be imported only after configuration by ORMConfigurator
    logging.info("Setting up ORM")
    _installed_apps = ["oc_delivery_apps.dlmanager"]
    
    from oc_orm_initializator.orm_initializator import OrmInitializator
    OrmInitializator(
            url=args.psql_url,
            user=args.psql_user,
            password=args.psql_password,
            installed_apps=_installed_apps)

    from oc_delivery_apps.dlmanager.models import Client

    active_clients = Client.objects.filter(is_active=True)

    if args.client:
        clients = active_clients.filter(code=args.client)
    else:
        clients = active_clients.all()

    # ftp_connect imports Django models, so import it there
    update_send_availability_statuses(clients, **args.__dict__)

