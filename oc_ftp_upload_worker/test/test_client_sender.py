#!/usr/bin/env python3

from . import django_settings
import django.test
import os
from ..ClientDeliverySender import EncryptingSender, SigningSender, ConnectionsContext
from ..upload_errors import DeliveryUploadError, ClientSetupError, EnvironmentSetupError, DeliveryExistsError, DeliveryEncryptionError
from oc_delivery_apps.dlmanager.models import Delivery, Client
from fs.tempfs import TempFS
from .test_keys import TestKeys
import gnupg
from types import MethodType
import posixpath

import logging
logging.getLogger().propagate = False
logging.getLogger().disabled = True

class SenderTestSuite(django.test.TransactionTestCase):

    def setUp(self):
        django.core.management.call_command('migrate', verbosity=0, interactive=False)
        self._kwargs = {
                "client_code": "SOMTEST",
                "country": "TestCountry"}

        Client(code=self._kwargs.get("client_code"), country=self._kwargs.get("country")).save()

    def tearDown(self):
        django.core.management.call_command('flush', verbosity=0, interactive=False)

    def get_basic_sender_params(self):
        client = Client.objects.get(code=self._kwargs.get("client_code"))
        nexus_fs = TempFS()
        base_ftp_fs = TempFS()
        base_ftp_fs._get_ftp = MethodType(lambda _self: None, base_ftp_fs)
        base_ftp_fs.makedirs(posixpath.join(self._kwargs.get("client_code"), "TO_BNK"))
        base_ftp_fs.makedirs(posixpath.join("PUBLIC", "CriticalPatch"))
        self._kwargs["mvn_artifact"] = f"com.example.{self._kwargs['client_code']}:{self._kwargs['client_code']}-test_delivery:v1.0:zip"
        nexus_fs.writetext(self._kwargs.get("mvn_artifact"), "hello")
        self._kwargs["client"] = client
        self._kwargs["context"] = ConnectionsContext(nexus_fs, base_ftp_fs)
        local_fs = TempFS()
        self._kwargs["work_fs"] = local_fs
        self._kwargs["pgp_private_key_password"] = "testkey"
        self._kwargs["pgp_private_key_file"] = "company.asc"

        with local_fs.openbin(self._kwargs.get("pgp_private_key_file"), "w") as company_key_file:
            company_key_file.write(TestKeys().get_key("company"))

        self._kwargs["pgp_private_key_file"] = local_fs.getsyspath(self._kwargs["pgp_private_key_file"])

class EncryptingSenderTestSuite(SenderTestSuite):

    def get_sender_params(self):
        self.get_basic_sender_params()
        repo_svn_fs = TempFS()
        self._kwargs["svn_client_keys_dir"] = posixpath.join("TestCountry", "SOMTEST", "data")
        self._kwargs["svn_client_public_key_file"] = posixpath.join(self._kwargs['svn_client_keys_dir'], "public.asc")
        repo_svn_fs.makedirs(self._kwargs["svn_client_keys_dir"])

        with repo_svn_fs.openbin(self._kwargs.get("svn_client_public_key_file"), "w") as client_public_key_file:
            client_public_key_file.write(TestKeys().get_key("client_pub"))

        self._kwargs["repo_svn_fs"] = repo_svn_fs

    def assert_sent_encrypted_content(self, ftp_fs, path, expected_content):
        with ftp_fs.openbin(path) as encrypted_file:
            encrypted_data = encrypted_file.read()
            self._assert_encrypted_with_key(encrypted_data, TestKeys().get_key("company"))
            self._assert_encrypted_with_key(encrypted_data, TestKeys().get_key("client_priv"))

    def _assert_encrypted_with_key(self, encrypted_data, key):
        with TempFS() as temp_fs:
            temp_dir = temp_fs.getsyspath(os.path.sep)
            gpg = gnupg.GPG(gnupghome=temp_dir)
            decryption_result = gpg.decrypt(encrypted_data, passphrase=self._kwargs.get("pgp_private_key_password"))
            self.assertFalse(decryption_result.ok)
            # self.assertEqual("decryption failed", decryption_result.status) # commented since message may be "decryption failed" or "no secret key"
            gpg.import_keys(key)
            decryption_result = gpg.decrypt(encrypted_data, passphrase=self._kwargs.get("pgp_private_key_password"))
            self.assertTrue(decryption_result.ok)

        # note: decryption result is bytes, regardless of we write source in text mode
        self.assertEqual(b"hello", decryption_result.data)

    def test_delivery_sent(self):
        self.get_sender_params()
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        sender.send_delivery(delivery)
        self.assertEqual(["SOMTEST-test_delivery-v1.0.pgp"], self._kwargs.get("context")[1].listdir(posixpath.join(self._kwargs.get("client_code"), "TO_BNK")))
        self.assert_sent_encrypted_content(self._kwargs.get("context")[1],
                                           posixpath.join(self._kwargs.get("client_code"), "TO_BNK", f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"),
                                           "hello")
        delivery.refresh_from_db()
        self.assertTrue(delivery.flag_uploaded)

    def test_foreign_delivery_skipped(self):
        self.get_sender_params()
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid="com.example.SOMOTHER",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        with self.assertRaises(DeliveryUploadError):
            sender.send_delivery(delivery)

        delivery.refresh_from_db()
        self.assertFalse(delivery.flag_uploaded)

    def test_no_public_sender_key_failure(self):
        self.get_sender_params()
        _wrong_key_file = os.path.join(os.path.sep, "tmp", "nonexistent.asc")
        self.assertFalse(os.path.exists(_wrong_key_file))
        self._kwargs["pgp_private_key_file"] = _wrong_key_file

        with self.assertRaises(EnvironmentSetupError):
            EncryptingSender(**self._kwargs)

    def test_no_receiver_key_failure(self):
        self.get_sender_params()
        self._kwargs["repo_svn_fs"].remove(self._kwargs["svn_client_public_key_file"])

        with self.assertRaises(ClientSetupError):
            EncryptingSender(**self._kwargs)

    def test_no_plain_delivery_failure(self):
        self.get_sender_params()
        self._kwargs.get("context")[0].remove(self._kwargs.get("mvn_artifact"))
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()

        with self.assertRaises(DeliveryUploadError):
            sender.send_delivery(delivery)

        delivery.refresh_from_db()
        self.assertFalse(delivery.flag_uploaded)

    def test_invalid_sender_key_failure(self):
        # suppose it caused by delivery itself, but it can also be because of broken keys
        self.get_sender_params()
        self._kwargs['work_fs'].writetext(posixpath.basename(self._kwargs['pgp_private_key_file']), "INVALID KEY FILE CONTENT")

        with self.assertRaises(DeliveryEncryptionError):
            EncryptingSender(**self._kwargs)

    def test_expired_key_failure(self):
        # suppose it caused by delivery itself, but it can also be because of broken keys
        self.get_sender_params()
        self._kwargs['repo_svn_fs'].writebytes(self._kwargs['svn_client_public_key_file'], TestKeys().get_key('expired_pub'))
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        with self.assertRaises(DeliveryEncryptionError):
            sender.send_delivery(delivery)

        delivery.refresh_from_db()
        self.assertFalse(delivery.flag_uploaded)

    def test_existing_ftp_delivery_failure(self):
        self.get_sender_params()
        self._kwargs.get("context")[1].writetext(posixpath.join(self._kwargs["client_code"], "TO_BNK", f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"), "existing")
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        sender.send_delivery(delivery)
        self.assertEqual([f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"], self._kwargs.get('context')[1].listdir(posixpath.join(self._kwargs['client_code'], "TO_BNK")))
        self.assert_sent_encrypted_content(self._kwargs.get('context')[1],
                                           posixpath.join(self._kwargs['client_code'], "TO_BNK", f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"),
                                           b"hello")
        delivery.refresh_from_db()
        self.assertTrue(delivery.flag_uploaded)

    def test_delivery_sent_twice(self):
        self.get_sender_params()
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        sender.send_delivery(delivery)
        sender.send_delivery(delivery)
        self.assertEqual([f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"], self._kwargs.get('context')[1].listdir(posixpath.join(self._kwargs['client_code'], "TO_BNK")))
        self.assert_sent_encrypted_content(self._kwargs.get('context')[1],
                                           posixpath.join(self._kwargs['client_code'], "TO_BNK", f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"),
                                           b"hello")
        delivery.refresh_from_db()
        self.assertTrue(delivery.flag_uploaded)

    def test_missing_data_subdir_failure(self):
        self.get_sender_params()
        self._kwargs.get('repo_svn_fs').removetree(posixpath.join(self._kwargs['country'], self._kwargs['client_code'], "data"))

        with self.assertRaises(ClientSetupError):
            EncryptingSender(**self._kwargs)

    def test_missing_ftp_subdir_failure(self):
        self.get_sender_params()
        self._kwargs.get('context')[1].removetree(posixpath.join(self._kwargs['client_code'], "TO_BNK"))
        sender = EncryptingSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()

        with self.assertRaises(ClientSetupError):
            sender.send_delivery(delivery)

    def test_get_destination_dir__default(self):
        self.get_sender_params()
        sender = EncryptingSender(**self._kwargs)
        self.assertEqual(sender._get_destination_dir(), posixpath.join("SOMTEST", "TO_BNK"))

    def test_get_destination_dir__override(self):
        self.get_sender_params()
        sender = EncryptingSender(**self._kwargs, dest={"enabled": True, "directory": posixpath.join("OTHERCLIENT", "OTHERDEST")})
        self.assertEqual(sender._get_destination_dir(), posixpath.join("OTHERCLIENT", "OTHERDEST"))

class SigningSenderTestSuite(SenderTestSuite):

    def get_sender_params(self):
        return self.get_basic_sender_params()

    def assert_sent_signed_content(self, ftp_fs, path, expected_content):
        with ftp_fs.openbin(path) as encrypted_file:
            encrypted_data = encrypted_file.read()
            self._assert_signed_with_key(encrypted_data, TestKeys().get_key("company_pub"))

    def _assert_signed_with_key(self, signed_data, key):
        with TempFS() as temp_fs:
            temp_dir = temp_fs.getsyspath(os.path.sep)
            gpg = gnupg.GPG(gnupghome=temp_dir)
            gpg.import_keys(key)
            result = gpg.verify(signed_data)
            self.assertTrue(result.valid)

    def test_no_private_sender_key_failure(self):
        self.get_sender_params()
        self._kwargs['work_fs'].remove(posixpath.basename(self._kwargs['pgp_private_key_file']))

        with self.assertRaises(EnvironmentSetupError):
            sender = SigningSender(**self._kwargs)

    def test_signing_needs_valid_private_sender_key(self):
        self.get_sender_params()
        self._kwargs['work_fs'].writetext(posixpath.basename(self._kwargs['pgp_private_key_file']), "INVALID KEY FILE CONTENT")

        with self.assertRaises(DeliveryEncryptionError):
            sender = SigningSender(**self._kwargs)

    def test_signing_needs_valid_passphrase(self):
        self.get_sender_params()
        self._kwargs['pgp_private_key_password'] = "INVALID"
        sender = SigningSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()

        with self.assertRaises(DeliveryEncryptionError):
            sender.send_delivery(delivery)

        delivery.refresh_from_db()
        self.assertFalse(delivery.flag_uploaded)

    def test_delivery_sent_signed(self):
        self.get_sender_params()
        sender = SigningSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        sender.send_delivery(delivery)
        self.assertEqual([f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"],
                          self._kwargs.get('context')[1].listdir(posixpath.join("PUBLIC", "CriticalPatch")))
        self.assert_sent_signed_content(self._kwargs.get('context')[1],
                    posixpath.join("PUBLIC", "CriticalPatch", f"{self._kwargs['client_code']}-test_delivery-v1.0.pgp"),
                    b"hello")
        delivery.refresh_from_db()
        self.assertTrue(delivery.flag_uploaded)

    def test_criticalpatches_dir_required(self):
        self.get_sender_params()
        self._kwargs.get('context')[1].removetree(posixpath.join("PUBLIC", "CriticalPatch"))
        sender = SigningSender(**self._kwargs)
        delivery = Delivery(groupid=f"com.example.{self._kwargs['client_code']}",
                            artifactid=f"{self._kwargs['client_code']}-test_delivery",
                            version="v1.0")
        delivery.save()
        with self.assertRaises(ClientSetupError):
            sender.send_delivery(delivery)

        delivery.refresh_from_db()
        self.assertFalse(delivery.flag_uploaded)

    def test_get_destination_dir__default(self):
        self.get_sender_params()
        sender = SigningSender(**self._kwargs)
        self.assertEqual(sender._get_destination_dir(), posixpath.join("PUBLIC", "CriticalPatch"))

    def test_get_destination_dir__override(self):
        self.get_sender_params()
        sender = SigningSender(**self._kwargs, dest={"enabled": True, "directory": posixpath.join("OTHERCLIENT", "OTHERDEST")})
        self.assertEqual(sender._get_destination_dir(), posixpath.join("OTHERCLIENT", "OTHERDEST"))
