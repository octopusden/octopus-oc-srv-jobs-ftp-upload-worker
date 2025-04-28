#!/usr/bin/env python3
""" Defines sequence of actions required to send delivery to client """


import gnupg
import logging
import os
import stat
import fs.osfs

from fs.copy import copy_file
from fs.move import move_file
from fs.tempfs import TempFS
from fs.errors import ResourceNotFound
from collections import namedtuple
from oc_cdtapi import NexusAPI
from .upload_errors import DeliveryUploadError, ClientSetupError, EnvironmentSetupError, DeliveryExistsError, \
    DeliveryEncryptionError, UploadProcessException
import posixpath


class ClientDeliverySender():
    """
    Abstract class which defines general algorithm for delivery send. 
    Its subclasses differ in delivery preprocessing.
    """

    def __init__(self, client, context, **kwargs):
        """
        :param str client: client which receives delivery
        :param tupe context: ConnectionsContext MVN with clean deliveries and FTP for outgoing deliveries
        :param **kwargs: data for external resources initialization, see worker arguemnts for description
        """
        self.client = client
        self.nexus_fs = context.nexus_fs
        self.ftp_fs = context.base_ftp_fs
        self.kwargs = kwargs

    def send_delivery(self, delivery):
        """ 
        Loads clean delivery, preprocesses it and sends it to client
        :param delivery: Delivery model instance to send 
        """
        self._validate_outgoing_delivery(delivery)
        target_dir = self._get_destination_dir()
        logging.info(f"Target directory for [{delivery.gav}]: [{target_dir}]")

        with TempFS() as temp_fs:
            clean_file_name = self._get_clean_delivery_content(delivery, temp_fs)
            processed_file_name = self._process_delivery_content(delivery, clean_file_name, temp_fs)
            self._upload_delivery(delivery, processed_file_name, temp_fs, target_dir)
        delivery.set_uploaded()

    def _process_delivery_content(self, delivery, clean_data_handle):
        """ 
        Hook for clean delivery preprocessing 
        :param delivery: Delivery model instance 
        :param clean_data_handle: file-like object pointing to delivery binary content
        """
        raise NotImplementedError("Subclasses must implement it")

    def _get_destination_dir(self):
        """ 
        Retrieves path to place prepared delivery
        :return str: target path relative to root
        """
        _p = self.kwargs.get("dest")

        if _p: 
            _p = _p.get("directory")

        return _p

    def _validate_outgoing_delivery(self, delivery):
        """ 
        Asserts that given delivery can be sent to client.
        :param dlmanager.Delivery delivery: delivery to check
        :raises: DeliveryUploadError
        """
        if delivery.client_name != self.client.code:
            raise DeliveryUploadError(f"Try to upload [{delivery.gav}] to wrong client [{delivery.client_name}]")

    def _get_clean_delivery_content(self, delivery, work_fs):
        """ 
        Retrieves clean delivery content.
        :param dlmanager.Delivery delivery: delivery to load
        :param fs.BaseFS work_fs: FS object to place loaded content
        :return str: path to file relative to work_fs root
        """
        gav_as_filename = _delivery_packaged_gav(delivery, "zip")

        try:
            clean_file_name = "clean_file"
            copy_file(self.nexus_fs, gav_as_filename, work_fs, clean_file_name)
        except ResourceNotFound as _e:
            raise DeliveryUploadError(f"Not found at MVN: [{gav_as_filename}]") from _e

        return clean_file_name

    def _upload_delivery(self, delivery, processed_file_name, work_fs, target_dir):
        """ 
        Uploads processed delivery to FTP 
        :param dlmanager.Delivery delivery: delivery to upload
        :param str processed_file_name: path to processed delivery file relative to work_fs root
        :param fs.BaseFS work_fs: FS object containing processed delivery
        :param str target_dir: path at FTP to place delivery
        """
        try:
            self._reconnect_ftp()
            target_fs = self.ftp_fs.opendir(target_dir)
            basename = NexusAPI.gav_to_filename(_delivery_packaged_gav(delivery, "pgp"))

            # if file exists - we have to overwrite it
            if target_fs.exists(basename):
                target_fs.remove(basename)

            move_file(work_fs, processed_file_name, target_fs, basename)
        except fs.errors.PermissionDenied as _pd:
            raise UploadProcessException(f"Permission denied when uploading [{basename}] for FTP: [{target_dir}]") from _pd
        except ResourceNotFound as _e:
            raise ClientSetupError(f"Not found on FTP: [{target_dir}]") from _e

    def _reconnect_ftp(self):
        """ 
        Forces FTPFS to recreate connection since delivery processing may run long and ftp timeout can occur
        """
        self.ftp_fs._ftp = None
        self.ftp_fs._get_ftp()


class EncryptingSender(ClientDeliverySender):
    """
    Encrypts delivery for client's and OW keys
    """

    def __init__(self, client, context, **kwargs):
        """
        :param str client: client which receives delivery
        :param tupe context: ConnectionsContext MVN with clean deliveries and FTP for outgoing deliveries
        :param repo_svn_fs: SvnFS pointing to client repo, used for keys reading
        :param **kwargs: data for external resources initialization, see worker arguemnts for description
        """
        super().__init__(client, context, **kwargs)
        svn_data_fs = self._get_svn_data_subdir(client, self.kwargs.get('repo_svn_fs'))
        self.encryption_keys = self._read_encryption_keys(svn_data_fs)

    def _get_svn_data_subdir(self, client, repo_svn_fs):
        client_data_path = posixpath.join(client.country, client.code, "data")

        try:
            return repo_svn_fs.opendir(client_data_path)
        except ResourceNotFound as _e:
            raise ClientSetupError(f"Not found in SVN: [{client_data_path}]") from _e

    def _read_encryption_keys(self, svn_data_fs):
        """ Prefetches client's and PGP keys that will be used for delivery encryption

        :param svn_data_fs: FS pointing to data/ directory in client's SVN root 
        :returns: list of keys represented as strings 
        """
        client_keys_names = list(filter(lambda arg: arg.endswith(".asc"), svn_data_fs.listdir(posixpath.sep)))
        encryption_keys = [read_key(svn_data_fs, key_name) for key_name in client_keys_names]

        if not client_keys_names:
            raise ClientSetupError(f"Client [{self.client.code}] has no public keys SVN")

        # check our key
        _key_fs = fs.osfs.OSFS(os.path.abspath(os.path.sep))
        _key_path = os.path.abspath(self.kwargs['pgp_private_key_file'])
        logging.debug(f"Appending our key: [{_key_path}]")

        if not _key_fs.exists(_key_path):
            raise EnvironmentSetupError(f"Not Found: [{_key_path}]")

        _key = read_key(_key_fs, _key_path)

        encryption_keys.append(_key)
        _validate_keys(encryption_keys)
        return encryption_keys

    def _process_delivery_content(self, delivery, clean_file_name, work_fs):
        """
        Encrypts delivery for fetched keys
        :param dlmanager.Delivery delivery: delivery to process
        :param str clean_file_name: target filename
        :param fs.BaseFS work_fs: filesystem where delivery clean file resides
        """
        processed_file_name = "processed_file"
        with TempFS() as temp_fs:
            gpg = _get_initialized_gpg(temp_fs, self.encryption_keys)
            temp_dir = temp_fs._temp_dir

            # large files can be processed by encrypt_file only
            output_path = os.path.join(temp_dir, processed_file_name)
            fingerprints = gpg.list_keys().fingerprints
            filename_args = _get_gpg_filename_args(delivery)

            with work_fs.openbin(clean_file_name) as clean_data_handle:
                encryption_result = gpg.encrypt_file(clean_data_handle, fingerprints, always_trust=True,
                                                     extra_args=filename_args, output=output_path)
            if encryption_result.ok:
                move_file(temp_fs, processed_file_name, work_fs, processed_file_name)
                return processed_file_name

        logging.error(encryption_result.stderr)
        raise DeliveryEncryptionError(f"Encryption failed: [{delivery.gav}]")

    def _get_destination_dir(self):
        """
        Places encrypted delivery to client/TO_BNK FTP folder
        """
        return super()._get_destination_dir() or posixpath.join(self.client.code, "TO_BNK")


class SigningSender(ClientDeliverySender):
    """
    Appends signature to delivery file
    """

    def __init__(self, client, context, **kwargs):
        """
        :param str client: client which receives delivery
        :param tupe context: ConnectionsContext MVN with clean deliveries and FTP for outgoing deliveries
        :param **kwargs: data for external resources initialization, see worker arguemnts for description
        """
        super().__init__(client, context, **kwargs)
        self._private_key_data = self._read_private_key()
        self.passphrase = self.kwargs['pgp_private_key_password']

    def _read_private_key(self):
        """
        Reads private key used to sign delivery 
        :return str: private key as string
        """
        # check our key
        _key_fs = fs.osfs.OSFS(os.path.abspath(os.path.sep))
        _key_path = os.path.abspath(self.kwargs['pgp_private_key_file'])
        logging.debug(f"Loading our key: [{_key_path}]")

        if not _key_fs.exists(_key_path):
            raise EnvironmentSetupError(f"NotFound: [{_key_path}]")

        private_key = read_key(_key_fs, _key_path)
        _validate_keys([private_key, ])
        return private_key

    def _get_destination_dir(self):
        """
        Signed deliveries are intended for multiple clients' usage so they are placed to common directory
        """
        return super()._get_destination_dir() or posixpath.join("PUBLIC", "CriticalPatch")

    def _process_delivery_content(self, delivery, clean_file_name, work_fs):
        """
        Writes file with both delivery and signature
        :param dlmanager.Delivery delivery: delivery to process
        :param str clean_file_name: a filename with delivery
        :param fs.BaseFS work_fs: filesystem where delivery is stored
        """
        processed_file_name = "processed_file"

        with TempFS() as temp_fs:
            gpg = _get_initialized_gpg(temp_fs, [self._private_key_data], passphrase=self.passphrase)
            # also clearsign should be disabled
            temp_dir = temp_fs._temp_dir
            output_path = os.path.join(temp_dir, processed_file_name)
            filename_args = _get_gpg_filename_args(delivery)

            with work_fs.openbin(clean_file_name) as clean_data_handle:
                sign_result = gpg.sign_file(clean_data_handle, passphrase=self.passphrase,
                                            binary=True, output=output_path,
                                            extra_args=filename_args, clearsign=False)

            if sign_result:  # truthy if signed successfully
                move_file(temp_fs, processed_file_name, work_fs, processed_file_name)
                return processed_file_name
            
        logging.error(sign_result.stderr)
        raise DeliveryEncryptionError(f"Signing failed: [{delivery.gav}]")


class MvnSender(ClientDeliverySender):
    """
    Uploads delivery to MVN
    """

    def _process_delivery_content(self, delivery, clean_data_handle, work_fs):
        return clean_data_handle

    def _get_destination_dir(self):
        logging.debug('MvnSender: reached _get_destination_dir')
        target_repo = self.kwargs["dest"].get('target_repo')
        logging.debug(f'target_repo: [{target_repo}]')
        return target_repo

    def _upload_delivery(self, delivery, processed_file_name, work_fs, target_dir):
        logging.debug(f'MvnSender: Reached _upload_delivery, target_dir: [{target_dir}]')
        na = NexusAPI.NexusAPI(
                root=self.kwargs["mvn_ext_url"],
                user=self.kwargs["mvn_ext_user"],
                auth=self.kwargs["mvn_ext_password"])

        with work_fs.openbin(processed_file_name) as data:
            external_gav = self._get_external_gav(delivery.gav)
            na.upload(external_gav, repo=target_dir, data=data)

            if not na.exists(external_gav, target_dir):
                raise NexusAPI.NexusAPIError(
                        f"MVN uploading failed. URL: [{self.kwargs['mvn_ext_url']}], GAV: [{external_gav}], repo: [{target_dir}]")

    def _get_external_gav(self, gav):
        logging.debug(f'Reached _get_external_gav. GAV: [{gav}]')
        return gav


class KeyValidation(object):
    """
    Validates the private key's passphrase
    """
    def __init__(self, key_path, passphrase, pgp_mail_from, mail_domain):
        """"
        Reads private key used to sign delivery
        :param key_path: private key path on local filesystem
        :param passphrase: passphrase for private key
        """
        if not key_path:
            raise EnvironmentSetupError("PGP_PRIVATE_KEY_PATH is not set")

        key_path = os.path.abspath(key_path)
        logging.info(f"Checking private key: [{key_path}]")

        if not os.path.exists(key_path):
            raise EnvironmentSetupError(f"Private key not found: [{key_path}]")

        private_key = read_key(fs.osfs.OSFS(os.path.abspath(os.path.sep)), key_path)
        _validate_private_keys([private_key], passphrase, pgp_mail_from, mail_domain)


ConnectionsContext = namedtuple("ConnectionsContext",
                                ["nexus_fs",  # nexus_fs: NexusFS with access to zip artifacts
                                 "base_ftp_fs" # base_ftp_fs: FtpFS pointing to root of FTP server
                                 ])


def _get_initialized_gpg(temp_fs, keys, passphrase=None):
    """
    Prepares GPG for usage 
    :param fs.BaseFS temp_fs: FS to place gnupghome
    :param list keys: list of keys to import 
    :return gnupg.GPG: GPG client instance 
    """
    temp_dir = temp_fs.getsyspath(os.path.sep)
    os.chmod(temp_dir, stat.S_IRWXU)
    gpg = gnupg.GPG(gnupghome=temp_dir)

    for key_data in keys:
        import_result = gpg.import_keys(key_data, passphrase=passphrase)
        if not import_result.fingerprints:
            logging.error(import_result.stderr)
            raise DeliveryEncryptionError(f"Unable to import key: '{key_data}")
    return gpg


def _validate_keys(keys):
    """
    raises exception if keys are invalid, otherwise returns normally
    :param list keys: list of gpg-keys data
    """
    with TempFS() as temp_fs:
        _get_initialized_gpg(temp_fs, keys)


def _validate_private_keys(keys, passphrase, pgp_mail_from, mail_domain):
    """
    Checks that the passphrase is correct
    """
    with TempFS() as temp_fs:
        gpg = _get_initialized_gpg(temp_fs, keys, passphrase=passphrase)
        message = "Encryption test"

        if '@' not in pgp_mail_from:
            pgp_mail_from = '@'.join([pgp_mail_from, mail_domain])

        encrypted = gpg.encrypt(message, pgp_mail_from, always_trust=True)
        encrypted_string = str(encrypted)
        decrypted = gpg.decrypt(encrypted_string, passphrase=passphrase)
        if not decrypted.ok:
            logging.error(decrypted.stderr)
            raise DeliveryEncryptionError("Private key passphrase validation failed.")
        logging.info('Validating private key can sign')
        signed = gpg.sign(message, keyid=pgp_mail_from, passphrase=passphrase)


def _delivery_packaged_gav(delivery, packaging):
    """
    Return GAV of packaged delivery
    :param dlmanager.Delivery delivery: delivery record
    :param str packaging: force delivery packaging
    """
    pkg_gav = NexusAPI.parse_gav(delivery.gav)
    pkg_gav["p"] = packaging
    return NexusAPI.gav_to_str(pkg_gav)


def _get_gpg_filename_args(delivery):
    """
    forces decrypt content as .txt file
    :param dlmanager.Delivery delivery: delivery record
    """
    embedded_filename = NexusAPI.gav_to_filename(_delivery_packaged_gav(delivery, "zip"))
    filename_args = ["--set-filename", embedded_filename]
    return filename_args


def read_key(fs, key_name):
    logging.debug(f"Reading key [{key_name}]")
    with fs.open(key_name, mode="rb") as key_file:
        return key_file.read()
