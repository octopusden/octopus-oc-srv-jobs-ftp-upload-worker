#!/usr/bin/env python3

from . import django_settings
import django.test
import os
import logging
from fs.tempfs import TempFS
from oc_delivery_apps.dlmanager.models import Client, FtpUploadClientOptions
from ..ClientDeliverySender import EncryptingSender, SigningSender, ConnectionsContext
from ..client_availability_update import is_client_encrypted_send_available, update_can_receive_status
import posixpath

import logging
logging.getLogger().propagate = False
logging.getLogger().disabled = True

class ClientStatusCheckTestSuite(django.test.TransactionTestCase):

    def setUp(self):
        django.core.management.call_command('migrate', verbosity=0, interactive=False)
        self._client_code = 'SOMTEST'
        self._country = 'SomeCountry'
        self._repo_svn_fs = TempFS()
        self._svn_data_dir = posixpath.join(self._country, self._client_code, "data")
        self._svn_key_file = posixpath.join(self._svn_data_dir, "pubkey.asc")
        self._repo_svn_fs.makedirs(self._svn_data_dir)
        self._repo_svn_fs.writetext(self._svn_key_file, "key content")
        self._client =  Client(code=self._client_code, country=self._country)
        self._ftp_fs = TempFS()
        self._ftp_data_dir = posixpath.join(self._client_code, "TO_BNK")
        self._ftp_fs.makedirs(self._ftp_data_dir)

    def tearDown(self):
        django.core.management.call_command('flush', verbosity=0, interactive=False)

    def test_svn_data_dir_required(self):
        self._repo_svn_fs.removetree(self._svn_data_dir)
        self.assertFalse(is_client_encrypted_send_available(self._client, self._repo_svn_fs, self._ftp_fs))

    def test_asc_files_required(self):
        self._repo_svn_fs.remove(self._svn_key_file)
        self.assertFalse(is_client_encrypted_send_available(self._client, self._repo_svn_fs, self._ftp_fs))

    def test_ftp_dir_required(self):
        self._ftp_fs.removetree(self._ftp_data_dir)
        self.assertFalse(is_client_encrypted_send_available(self._client, self._repo_svn_fs, self._ftp_fs))

    def test_configured_client_validated(self):
        self.assertTrue(is_client_encrypted_send_available(self._client, self._repo_svn_fs, self._ftp_fs))


class ClientStatusUpdateTestSuite(django.test.TransactionTestCase):
    def setUp(self):
        django.core.management.call_command('migrate', verbosity=0, interactive=False)
        self._client_code = 'SOMTEST'
        self._country = 'SomeCountry'

    def tearDown(self):
        django.core.management.call_command('flush', verbosity=0, interactive=False)

    def test_status_changed(self):
        client, _ = Client.objects.get_or_create(code=self._client_code, country=self._country)
        FtpUploadClientOptions(client=client, can_receive=True).save()
        update_can_receive_status(client, False)
        client, _ = Client.objects.get_or_create(code=self._client_code, country=self._country)  # reload
        self.assertFalse(client.ftpuploadclientoptions.can_receive)

    def test_signing_client_always_available(self):
        client, _ = Client.objects.get_or_create(code=self._client_code, country=self._country)
        FtpUploadClientOptions(client=client, can_receive=True, should_encrypt=False).save()
        update_can_receive_status(client, False)
        client, _ = Client.objects.get_or_create(code=self._client_code, country=self._country)  # reload
        self.assertTrue(client.ftpuploadclientoptions.can_receive)

    def test_status_set(self):
        client, _ = Client.objects.get_or_create(code=self._client_code, country=self._country)
        update_can_receive_status(client, False)
        self.assertFalse(client.ftpuploadclientoptions.can_receive)
