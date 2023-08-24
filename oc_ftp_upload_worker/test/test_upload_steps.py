#!/usr/bin/env python3

from . import django_settings
import django.test
import os
from fs.tempfs import TempFS
from types import MethodType
from oc_delivery_apps.checksums.models import Files, Locations, CiTypes, LocTypes
from oc_delivery_apps.dlmanager.models import Delivery, Client, ClientEmailAddress, FtpUploadClientOptions
from ..upload_steps import get_pending_deliveries, notify_client
from ..independent_upload import process_client_deliveries_independently, process_clients_independently
from ..upload_errors import DeliveryUploadError, ClientSetupError, EnvironmentSetupError
from ..ClientDeliverySender import ConnectionsContext, EncryptingSender
from .test_keys import TestKeys
import posixpath

import logging
logging.getLogger().propagate = False
logging.getLogger().disabled = True
import tempfile


class UploadStepsBaseTestCase(django.test.TransactionTestCase):

    def setUp(self):
        django.core.management.call_command('migrate', verbosity=0, interactive=False)
        self._kwargs = dict()
        self._kwargs["client_code_1"] = 'SOMTEST'
        self._kwargs["client_code_2"] = 'SOMOTHER'
        self._kwargs['country'] = 'TestCountry'
        Client(code=self._kwargs['client_code_1'], country=self._kwargs['country'], is_active=True).save()
        Client(code=self._kwargs['client_code_2'], country=self._kwargs['country']).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_1']}", artifactid="a", version="v1",
                 flag_approved=False, flag_uploaded=False, flag_failed=False, pk=1).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_1']}", artifactid="a", version="v2",
                 flag_approved=True, flag_uploaded=False, flag_failed=False, pk=2).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_1']}", artifactid="a", version="v3",
                 flag_approved=True, flag_uploaded=True, flag_failed=False, pk=3).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_1']}", artifactid="a", version="v4",
                 flag_approved=True, flag_uploaded=False, flag_failed=True, pk=4).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_2']}", artifactid="a", version="v5",
                 flag_approved=True, flag_uploaded=False, flag_failed=False, pk=5).save()
        Delivery(groupid=f"g.{self._kwargs['client_code_1']}", artifactid="a", version="v6", mf_delivery_author="author",
                 flag_approved=True, flag_uploaded=False, flag_failed=False, pk=6).save()
        ClientEmailAddress(clientid=Client.objects.get(code=self._kwargs['client_code_1']),
                           email_address="foobar@example.com").save()
        LocTypes(code="NXS").save()

    def tearDown(self):
        django.core.management.call_command('flush', verbosity=0, interactive=False)

        for _k, _v in self._kwargs.items():
            if hasattr(_v, "close"):
                _v.close()

class MockSender(object):

    def __init__(self):
        self.logged_calls = list()

    def send_delivery(self, delivery):
        self.logged_calls.append(delivery.pk)

class MockMailer(object):

    def __init__(self):
        self.logged_send = list()

    def send_email(self, to_addresses, subject, text):
        self.logged_send.append({
            "to_addresses": to_addresses,
            "subject": subject,
            "text": text})

class PendingQueueTestSuite(UploadStepsBaseTestCase):

    def test_all_pending_deliveries(self):
        pending_deliveries = get_pending_deliveries()
        self.assertListEqual(sorted([2, 5, 6]), sorted([dlv.pk for dlv in pending_deliveries]))

    def test_deleted_deliveries_ignored(self):
        citype, _ = CiTypes.objects.get_or_create(code="TEST")
        delivery_file, _ = Files.objects.get_or_create(ci_type=citype)
        at_nexus, _ = LocTypes.objects.get_or_create(code="NXS")
        delivery_location = Locations(file=delivery_file, loc_type=at_nexus,
                                      path=Delivery.objects.get(pk=2).gav)
        delivery_location.save()
        delivery_location.delete()
        pending_deliveries = get_pending_deliveries()
        self.assertListEqual(sorted([5, 6]), sorted([dlv.pk for dlv in pending_deliveries]))

    def test_delivery_without_history_accepted(self):
        Delivery.objects.get(pk=2).history.all().delete()
        pending_deliveries = get_pending_deliveries()
        self.assertListEqual(sorted([2, 5, 6]), sorted([dlv.pk for dlv in pending_deliveries]))

class ClientProcessingTestSuite(UploadStepsBaseTestCase):

    def get_sender_params(self):
        self._kwargs["client"] = Client.objects.get(code=self._kwargs['client_code_1'])
        self._kwargs["local_fs"] = TempFS()
        self._kwargs["repo_svn_fs"]= TempFS()
        self._kwargs["svn_data_dir_1"] = posixpath.join(self._kwargs['country'], self._kwargs['client_code_1'], "data")
        self._kwargs['repo_svn_fs'].makedirs(self._kwargs['svn_data_dir_1'])
        self._kwargs['mvn_fs'] = TempFS()
        self._kwargs['ftp_fs'] = TempFS()
        self._kwargs['ftp_fs']._get_ftp = MethodType(lambda _self: None, self._kwargs['ftp_fs'])
        self._kwargs['ftp_data_dir_1'] = posixpath.join(self._kwargs['client_code_1'], "TO_BNK")
        self._kwargs['ftp_data_dir_2'] = posixpath.join('PUBLIC', "CriticalPatch")
        self._kwargs['ftp_fs'].makedirs(self._kwargs['ftp_data_dir_1'])
        self._kwargs['context'] = ConnectionsContext(self._kwargs['mvn_fs'], self._kwargs['ftp_fs'])
        self._kwargs['svn_public_key_file_1'] = posixpath.join(self._kwargs['svn_data_dir_1'], "client_pub.asc")
        self._kwargs['pgp_private_key_file'] = 'company.asc'

        with self._kwargs['repo_svn_fs'].openbin(self._kwargs['svn_public_key_file_1'], "w") as client_public_key_file:
            client_public_key_file.write(TestKeys().get_key('client_pub'))
        with self._kwargs['local_fs'].openbin(self._kwargs['pgp_private_key_file'], "w") as company_key_file:
            company_key_file.write(TestKeys().get_key('company'))
        self._kwargs['pgp_private_key_password'] = "testkey"
        self._kwargs['pgp_private_key_file'] = self._kwargs['local_fs'].getsyspath(self._kwargs['pgp_private_key_file'])

        self._kwargs['mvn_fs'].writetext(f"g.{self._kwargs['client_code_1']}:a:v2:zip", "hello")
        self._kwargs['mvn_fs'].writetext(f"g.{self._kwargs['client_code_1']}:a:v6:zip", "hello")
        self._kwargs['delivery_destinations_file_obj'] = tempfile.NamedTemporaryFile(suffix='.yml')
        self._kwargs['delivery_destinations_file'] = self._kwargs['delivery_destinations_file_obj'].name

    def test_normal_upload(self):
        to_process = get_pending_deliveries().filter(groupid__endswith=self._kwargs['client_code_1'])
        sender = MockSender()
        result = process_client_deliveries_independently(to_process, sender)
        self.assertListEqual(sorted([2, 6]), sorted([dlv.pk for dlv in result.sent_deliveries]))

    def test_single_fails_skipped(self):
        class FailingMockSender(object):
            def send_delivery(self, delivery):
                if delivery.pk == 6:
                    raise DeliveryUploadError("fail")

        to_process = get_pending_deliveries().filter(groupid__endswith=self._kwargs['client_code_1'])
        result = process_client_deliveries_independently(to_process, FailingMockSender())
        self.assertListEqual(sorted([2]), sorted([dlv.pk for dlv in result.sent_deliveries]))
        self.assertEqual(1, len(result.raised_errors))

    def test_single_client_fails_skipped(self):
        # no any setup for second client
        self.get_sender_params()
        self._kwargs.pop('client')
        result = process_clients_independently(get_pending_deliveries(),
                                               Client.objects.all(), **self._kwargs)
        self.assertListEqual(sorted([2, 6]), sorted([dlv.pk for dlv in result.sent_deliveries]))
        uploaded_deliveries = Delivery.objects.filter(flag_uploaded=True)
        self.assertListEqual(sorted([2, 6, 3]), sorted([dlv.pk for dlv in uploaded_deliveries]))
        self.assertEqual(1, len(result.raised_errors))
        self.assertIsInstance(result.raised_errors.pop(0), ClientSetupError)

    def test_only_receiving_client_processed(self):
        # no any setup for second client
        client = Client.objects.get(code=self._kwargs['client_code_1'])
        FtpUploadClientOptions(client=client, can_receive=False).save()
        self.get_sender_params()
        self._kwargs.pop('client')
        result = process_clients_independently(get_pending_deliveries(),
                                               Client.objects.all(), **self._kwargs)
        self.assertEqual(0, len([dlv.pk for dlv in result.sent_deliveries]))

    def test_critical_patches_sent_signed(self):
        # current implementation cannot be tested with mocks
        # as ClientDeliverySender is created inside method
        # here we testing that delivery is sent without access to missing FTP dir
        # which means we used signing instead of encryption
        self.get_sender_params()
        FtpUploadClientOptions(client=self._kwargs['client'], should_encrypt=False).save()
        delivery = Delivery(groupid=f"g.{self._kwargs['client'].code}", artifactid="a", version="v5",
                 flag_approved=True, flag_uploaded=False, flag_failed=False, pk=10)
        delivery.save()
        self._kwargs['context'][0].writetext(delivery.gav, "hello")
        self._kwargs.pop('client')
        self._kwargs['ftp_fs'].makedirs(self._kwargs['ftp_data_dir_2'])

        result = process_clients_independently(get_pending_deliveries(),
                                               Client.objects.filter(is_active=True), **self._kwargs)

        self.assertEqual(0, len(result.raised_errors))
        self.assertListEqual(sorted([2, 6, 10]), sorted([dlv.pk for dlv in result.sent_deliveries]))
        uploaded_deliveries = Delivery.objects.filter(flag_uploaded=True)
        self.assertListEqual(sorted([2, 6, 3, 10]), sorted([dlv.pk for dlv in uploaded_deliveries]))

    def test_critical_error_raised(self):
        self.get_sender_params()
        self._kwargs.get('local_fs').remove(posixpath.basename(self._kwargs.get('pgp_private_key_file')))
        self._kwargs.pop('client')
        with self.assertRaises(EnvironmentSetupError):
            process_clients_independently(get_pending_deliveries(),
                                          Client.objects.all(), **self._kwargs)

        already_uploaded_deliveries = Delivery.objects.filter(flag_uploaded=True)
        self.assertListEqual([3], [dlv.pk for dlv in already_uploaded_deliveries])


class ClientNotificationTestSuite(UploadStepsBaseTestCase):

    def get_sender_params(self):
        self._kwargs['delivery_destinations_file_obj'] = tempfile.NamedTemporaryFile(suffix='.yml')
        self._kwargs['delivery_destinations_file'] = self._kwargs['delivery_destinations_file_obj'].name
        self.mailer = MockMailer()

    def test_no_deliveries_to_send(self):
        self.get_sender_params()
        deliveries = []
        notify_client(self.mailer, Client.objects.get(code=self._kwargs['client_code_1']), deliveries, **self._kwargs)
        self.assertEqual(0, len(self.mailer.logged_send))

    def test_other_client_deliveries_rejected(self):
        self.get_sender_params()
        deliveries = [Delivery.objects.get(pk=5)]
        with self.assertRaises(ValueError):
            notify_client(self.mailer, Client.objects.get(code=self._kwargs['client_code_1']), deliveries, **self._kwargs)

    def test_notifications_sent(self):
        self.get_sender_params()
        deliveries = Delivery.objects.filter(pk__in=[2, 6])
        self._kwargs['mail_domain'] = 'mail.example.com'
        notify_client(self.mailer, Client.objects.get(code=self._kwargs['client_code_1']), deliveries, **self._kwargs)
        self.assertEqual(2, len(self.mailer.logged_send))
        self.assertIn(f"author@{self._kwargs['mail_domain']}", self.mailer.logged_send.pop(1).get("to_addresses"))

    def test_no_recipients_specified(self):
        self.get_sender_params()
        self._kwargs['mail_domain'] = 'mail.example.com'
        deliveries = Delivery.objects.filter(pk__in=[2, 6])
        ClientEmailAddress.objects.all().delete()
        notify_client(self.mailer, Client.objects.get(code=self._kwargs['client_code_1']), deliveries, **self._kwargs)
        self.assertEqual(1, len(self.mailer.logged_send))
        self.assertIn(f"author@{self._kwargs['mail_domain']}", self.mailer.logged_send.pop(0).get("to_addresses"))
