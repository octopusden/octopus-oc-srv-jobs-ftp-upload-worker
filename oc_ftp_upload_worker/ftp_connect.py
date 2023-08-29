#! /usr/bin/env python3
""" Top-level script to run delivery upload """

import os
import re
from oc_mailer.Mailer import Mailer
from .fs_clients import get_svn_fs_client, get_ftp_fs_client, get_mvn_fs_client, get_smtp_client
from fs.tempfs import TempFS
from .ClientDeliverySender import EncryptingSender, SigningSender, ConnectionsContext
from .upload_errors import DeliveryExistsError, EnvironmentSetupError, UploadProcessException, DeliveryUploadError, ClientSetupError, EnvironmentSetupError, UploadProcessException, DeliveryEncryptionError
import pkg_resources
import logging
import sys


def perform_upload(clients, **kwargs):
    """ Runs upload process for each client 

    :param clients: list of clients to process
    :param **kwargs: keyword options, see worker command line arguments for description
    """
    # following resources are common for all client connections.
    # ClientDeliverySender objects itself create separate connections to concrete client subdir.

    ## make credentials for two MVN connections
    with TempFS() as work_fs, \
            get_svn_fs_client(
                    url=kwargs['svn_clients_url'],
                    user=kwargs['svn_clients_user'],
                    password=kwargs['svn_clients_password']) as repo_svn_fs, \
            get_mvn_fs_client(url=kwargs['mvn_int_url'],
                    user=kwargs['mvn_int_user'],
                    password=kwargs['mvn_int_password'],
                    work_fs=work_fs,
                    download_repo=kwargs['mvn_download_repo']) as nexus_fs, \
            get_ftp_fs_client(
                    url=kwargs['ftp_url'],
                    user=kwargs['ftp_user'],
                    password=kwargs['ftp_password']) as base_ftp_fs:
        context = ConnectionsContext(nexus_fs, base_ftp_fs)
        from .upload_steps import get_pending_deliveries, notify_deliveries_recipients
        deliveries = get_pending_deliveries()
        from .independent_upload import process_clients_independently
        upload_result = process_clients_independently(deliveries, clients, context,
                                                      repo_svn_fs, **kwargs)

        smtp_client = get_smtp_client(url=kwargs['smtp_url'],
                user=kwargs['smtp_user'],
                password=kwargs['smtp_password'])

        mail_from = kwargs['mail_from']

        if '@' not in mail_from:
            mail_from = '@'.join([mail_from, kwargs['mail_domain']])

        mailer = Mailer(smtp_client, mail_from, config_path=kwargs['mail_config_file'])

        notify_deliveries_recipients(mailer, clients, upload_result.sent_deliveries, **kwargs)
        smtp_client.quit()

        postprocess_upload_result(upload_result)


def postprocess_upload_result(upload_result):
    """
    Informs user about upload result. Raises error if severe errors occured
    :param upload_result: UploadResult instance 
    :raises: UploadProcessException 
    """
    if upload_result.sent_deliveries:
        deliveries_info = ":".join([dlv.gav for dlv in upload_result.sent_deliveries])
        logging.info(f"Uploaded deliveries: [{deliveries_info}]")
    else:
        logging.info("No deliveries were uploaded")

    if upload_result.raised_errors:
        logging.error(f"Upload exceptions: [{', '.join(list(map(lambda x: str(x), upload_result.raised_errors)))}]")

        # errors caused by existing deliveries are considered as valid case
        severe_errors = [err for err in upload_result.raised_errors
                         if not isinstance(err, DeliveryExistsError)]
        if severe_errors:
            raise UploadProcessException("Some deliveries were not uploaded. See log for details")
        else:
            logging.warning("Some non-critical errors occured. See log for details")


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Run deliveries uploading to FTP")
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

    ### MVN (maven) arguments
    parser.add_argument("--mvn-url", dest="mvn_url", help="MVN URL",
                        default=os.getenv("MVN_URL"))
    parser.add_argument("--mvn-user", dest="mvn_user", help="MVN user",
                        default=os.getenv("MVN_USER"))
    parser.add_argument("--mvn-password", dest="mvn_password", help="MVN password",
                        default=os.getenv("MVN_PASSWORD"))
    parser.add_argument("--mvn-int-url", dest="mvn_int_url", help="MVN internal URL",
                        default=os.getenv("MVN_INT_URL") or os.getenv("MVN_URL"))
    parser.add_argument("--mvn-int-user", dest="mvn_int_user", help="MVN internal user",
                        default=os.getenv("MVN_INT_USER") or os.getenv("MVN_USER"))
    parser.add_argument("--mvn-int-password", dest="mvn_int_password", help="MVN internal password",
                        default=os.getenv("MVN_INT_PASSWORD") or os.getenv("MVN_PASSWORD"))
    parser.add_argument("--mvn-download-repo", dest="mvn_download_repo", 
                        help="MVN repository to download deliveries from",
                        default=os.getenv("MVN_DOWNLOAD_REPO") or "maven-virtual")
    parser.add_argument("--mvn-ext-url", dest="mvn_ext_url", help="MVN external URL",
                        default=os.getenv("MVN_EXT_URL") or os.getenv("MVN_URL"))
    parser.add_argument("--mvn-ext-user", dest="mvn_ext_user", help="MVN external user",
                        default=os.getenv("MVN_EXT_USER") or os.getenv("MVN_USER"))
    parser.add_argument("--mvn-ext-password", dest="mvn_ext_password", help="MVN external password",
                        default=os.getenv("MVN_EXT_PASSWORD") or os.getenv("MVN_PASSWORD"))
    parser.add_argument("--mvn-link-url", dest="mvn_link_url", help="MVN external URL",
                        default=os.getenv("MVN_LINK_URL") or os.getenv("MVN_EXT_URL") or os.getenv("MVN_URL"))
    parser.add_argument("--delivery-destinations-file", dest="delivery_destinations_file", 
                        help="Path to delivery_destinations.yaml",
                        default=os.path.abspath(os.getenv("DELIVERY_DESTINATIONS_FILE") or \
                                os.path.join(os.getcwd(), "delivery_destinations.yaml")))
    parser.add_argument("--external-repo-prefix-url-tmpl", dest="external_repo_prefix_url_tmpl", 
                            help="Template for external delivery MVN link",
                            default=os.getenv("EXTERNAL_REPO_PREFIX_URL_TMPL") or \
                                    '${MVN_LINK_URL}/${CLIENT_REPO}/${FULL_GAV}')

    ### SMTP (mailer) arguments
    parser.add_argument("--smtp-url", dest="smtp_url", help="SMTP URL",
                        default=os.getenv("SMTP_URL"))
    parser.add_argument("--smtp-user", dest="smtp_user", help="SMTP user",
                        default=os.getenv("SMTP_USER"))
    parser.add_argument("--smtp-password", dest="smtp_password", help="SMTP password",
                        default=os.getenv("FTP_PASSWORD"))
    parser.add_argument("--mail-domain", dest="mail_domain", 
                        help="Mail domain to be added to delivery author e-mail address",
                        default=os.getenv("MAIL_DOMAIN") or "example.com")
    parser.add_argument("--mail-from", dest="mail_from", 
                        help="Mail user to be set as the notification sender in FROM section",
                        default=os.getenv("MAIL_FROM") or os.getenv("SMTP_USER"))
    parser.add_argument("--mail-config-file", dest="mail_config_file", help="Mailer configuration file",
                        default=os.path.abspath(os.getenv("MAIL_CONFIG_FILE") or 
                            pkg_resources.resource_filename("oc_ftp_upload_worker", 
                                os.path.join("resources", "mailer", "config.json"))))

    ### GPG options
    parser.add_argument("--pgp-private-key-file", dest="pgp_private_key_file", help="Path to PGP private key file",
                        default=os.path.abspath(os.getenv("PGP_PRIVATE_KEY_FILE") or \
                                os.path.join(os.getcwd(), "private_key.asc")))
    parser.add_argument("--pgp-private-key-password", dest="pgp_private_key_password", 
                        help="Password for PGP private key",
                        default=os.getenv("PGP_PRIVATE_KEY_PASSWORD"))
    parser.add_argument("--pgp-mail-from", dest="pgp_mail_from", 
                        help="Mail user to be set as the notification sender in FROM section",
                        default=os.getenv('PGP_MAIL_FROM') or os.getenv("MAIL_FROM") or os.getenv("SMTP_USER"))


    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)

    # fix arguments since 'argparse' has no option to do so
    __override = {
            "mvn_ext_url": "mvn_url",
            "mvn_ext_user": "mvn_user",
            "mvn_ext_password": "mvn_password",
            "mvn_int_url": "mvn_url",
            "mvn_int_user": "mvn_user",
            "mvn_int_password": "mvn_password",
            "mvn_link_url": ["mvn_ext_url", "mvn_url"],
            "mail_from": ["smtp_user"],
            "pgp_mail_from": ["mail_from", "smtp_user"]}

    for _k, _v in __override.items():

        if getattr(args, _k):
            logging.debug(f"Not overriding [{_k}]: is set.")
            continue

        if not _v:
            logging.debug(f"Not overriding [{_k}]: not mapped correctly")
            continue

        if not isinstance(_v, list):
            _v = [_v]

        for _vv in _v:
            if not getattr(args, _vv):
                logging.debug(f"Not overriding [{_k}] with [{_vv}]: last one is not set")
                continue

            logging.debug(f"Overriding [{_k}] <== [{_vv}]")
            setattr(args, _k, getattr(args, _vv))
            break
    
    #Log args:
    for _k, _v in args.__dict__.items():
        _display_value = _v

        if _v and _k.endswith('password'):
            _display_value = '*'*len(_v)

        logging.info(f"{_k.upper()}:\t[{_display_value}]")


    # ORM-related stuff must be imported only after configuration by ORMConfigurator
    logging.info("Setting up ORM")
    _installed_apps = ["oc_delivery_apps.dlmanager", "oc_delivery_apps.checksums"]
    
    from oc_orm_initializator.orm_initializator import OrmInitializator
    OrmInitializator(
            url=args.psql_url,
            user=args.psql_user,
            password=args.psql_password,
            installed_apps=_installed_apps)

    logging.info("ORM initialized")

    from oc_delivery_apps.dlmanager.models import Client

    active_clients = Client.objects.filter(is_active=True)

    if args.client:
        clients = active_clients.filter(code=args.client)
    else:
        clients = active_clients.all()

    _kwargs = args.__dict__

    # 'client' argument is now processed and may confuse latter rountines
    _kwargs.pop('client')

    # ftp_connect imports Django models, so import it there
    perform_upload(clients, **_kwargs)
