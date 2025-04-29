#!/usr/bin/env python3

from oc_dlinterface.dlupload_worker_interface import queue_published, UploadWorkerServer
import os
import argparse
import logging
from oc_orm_initializator.orm_initializator import OrmInitializator
import pkg_resources
from oc_logging.Logging import setup_logging

class UploadWorkerApplication(UploadWorkerServer):

    def __init__(self, *args, **kvargs):
        """
        Basic constructor
        :param bool setup_orm: do or not setup django ORM
        """
        self.setup_orm = kvargs.pop('setup_orm', True)
        super().__init__(*args, **kvargs)

    def __fix_args(self, args):
        """
        Do override some arguments since this may not be done by 'argparse' itself\
        :param argparse.namespace args:
        """
        logging.debug("Fixing arguments")

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

        return args


    def init(self, args):
        """
        Initialization of the parameters from arguments
        :param argparse.namespace args: parsed arguments
        """
        setup_logging()
        args = self.__fix_args(args)

        # just log the arguments
        for _k, _v in args.__dict__.items():
            _display_value = _v 

            if _v and _k.endswith('password'):
                _display_value = '*'*len(_v)

            logging.info(f"{_k.upper()}:\t[{_display_value}]")

        if self.args.pgp_check.lower() in ['y', 'yes', 'true']:
            logging.info("Validating private key's passphrase")
            from .ClientDeliverySender import KeyValidation
            KeyValidation(args.pgp_private_key_file, args.pgp_private_key_password,
                    args.pgp_mail_from, args.mail_domain)

        if not self.setup_orm:
            return

        logging.info("Setting up ORM")
        _installed_apps = ["oc_delivery_apps.dlmanager", "oc_delivery_apps.checksums"]

        OrmInitializator(
            url=self.args.psql_url,
            user=self.args.psql_user,
            password=self.args.psql_password,
            installed_apps=_installed_apps)

        logging.info("ORM initialization done")

    def get_client_info(self, client=None):
        """
        Get client information from database
        :param str client: client code
        :return dlmanager.Client: Client instance records from db (iterable)
        """
        from oc_delivery_apps.dlmanager.models import Client
        active_clients = Client.objects.filter(is_active=True)
        return active_clients.filter(code=client) if client else active_clients.all()

    def ping(self):
        "Just check worker is OK"
        return

    def prepare_parser(self):
        """
        Prepare argument parser descriptoin
        :return argparse.ArgumentParser:
        """
        return argparse.ArgumentParser(description="Delivery upload worker")

    def upload_delivery(self, client):
        """
        Do upload delivery for a client called
        """
        self.client_availability_update(client)
        self.upload_to_ftp(client)

    def client_availability_update(self, client):
        """
        Update a client availability
        :param str client: client code
        """
        if not client:
            raise ValueError('Client code must be specified')

        client = self.get_client_info(client)
        from .client_availability_update import update_send_availability_statuses
        update_send_availability_statuses(client, **self.args.__dict__)

    def upload_to_ftp(self, client):
        """
        Upload all pending deliveries for a client given
        :param str client: client code
        """

        if not client:
            raise ValueError('Client code must be specified')

        client = self.get_client_info(client)

        from .ftp_connect import perform_upload
        perform_upload(client, **self.args.__dict__)

    def custom_args(self, parser):
        """
        Append specific arguments for this worker
        :param argparse.ArgumentParser parser: parser with arguments
        :return argparse.ArgumentParse: modified parser with additional arguments
        """
        # AMQP-related arguments are described in parent class

        ### PSQL (django-database) arguments
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
                            default=os.path.abspath(os.getenv("DELIVERY_DESTINATIONS_FILE") \
                                    or os.path.join(os.getcwd(), "delivery_destinations.yaml")))
        parser.add_argument("--external-repo-prefix-url-tmpl", dest="external_repo_prefix_url_tmpl", 
                            help="Template for external delivery MVN link",
                            default=os.getenv("EXTERNAL_REPO_PREFIX_URL_TMPL") or \
                                    '${MVN_LINK_URL}/${CLIENT_REPO}/${FULL_GAV}')

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

        ### SMTP (mailer) arguments
        parser.add_argument("--smtp-url", dest="smtp_url", help="SMTP URL",
                            default=os.getenv("SMTP_URL"))
        parser.add_argument("--smtp-user", dest="smtp_user", help="SMTP user",
                            default=os.getenv("SMTP_USER"))
        parser.add_argument("--smtp-password", dest="smtp_password", help="SMTP password",
                            default=os.getenv("FTP_PASSWORD"))
        parser.add_argument("--mail-domain", dest="mail_domain", help="Mail domain to be added to delivery author e-mail address",
                            default=os.getenv("MAIL_DOMAIN") or "example.com")
        parser.add_argument("--mail-from", dest="mail_from", 
                            help="Mail user to be set as the notification sender in FROM section",
                            default=os.getenv("MAIL_FROM") or os.getenv("SMTP_USER"))
        parser.add_argument("--mail-config-file", dest="mail_config_file", help="Mailer configuration file",
                            default=os.path.abspath(os.getenv("MAIL_CONFIG_FILE") or \
                                pkg_resources.resource_filename(
                                    "oc_ftp_upload_worker", os.path.join("resources", "mailer", "config.json"))))

        ### GPG options
        parser.add_argument("--pgp-check", dest="pgp_check", help="Enable or disable PGP private key check",
                            default=os.getenv("PGP_CHECK", "True"))
        parser.add_argument("--pgp-private-key-file", dest="pgp_private_key_file", help="Path to PGP private key file",
                            default=os.path.abspath(os.getenv("PGP_PRIVATE_KEY_FILE") or os.path.join(os.getcwd(), "private_key.asc")))
        parser.add_argument("--pgp-private-key-password", dest="pgp_private_key_password", help="Password for PGP private key",
                            default=os.getenv("PGP_PRIVATE_KEY_PASSWORD"))
        parser.add_argument("--pgp-mail-from", dest="pgp_mail_from",
                            help="Mail user to be set as the notification sender in FROM section",
                            default=os.getenv('PGP_MAIL_FROM') or os.getenv("MAIL_FROM") or os.getenv("SMTP_USER"))

        return parser



if __name__ == '__main__':
    exit(UploadWorkerApplication().main())
