from . import django_settings
import django.test
from oc_cdt_queue2.test.synchron.mocks.queue_loopback import LoopbackConnection, global_messaging, global_message_queue
from oc_dlinterface.dlupload_worker_interface import UploadWorkerClient
from ..upload_worker import UploadWorkerApplication
from oc_delivery_apps.dlmanager.models import Client
import os
import unittest.mock


class UploadWorkerTest(django.test.TransactionTestCase):

    def _get_client_info(self):
        return Client.objects.filter(is_active=True, code=self.client_code)

    def setUp(self):
        django.core.management.call_command('migrate', verbosity=0, interactive=False)
        self.client_code = 'SOMTEST'
        self.country = 'TestCountry'
        Client(code=self.client_code, country=self.country, is_active=True).save()
        self.app = UploadWorkerApplication(setup_orm=False)

        self.app.args = unittest.mock.MagicMock()
        self.app.args.pgp_check = False
        self.app.args.svn_clients_url = "https://svn.test.example.com/svn"
        self.app.args.svn_clients_user = "test"
        self.app.args.svn_clients_password = "test"

    def tearDown(self):
        django.core.management.call_command('flush', verbosity=0, interactive=False)

    def test_ping(self):
        self.assertIsNone(self.app.ping())

    def test_client_availability_update(self):
        clients = self._get_client_info()
        with unittest.mock.patch('oc_ftp_upload_worker.client_availability_update.update_send_availability_statuses') as _x:
            self.assertIsNone(self.app.client_availability_update(clients))
            _x.assert_called_once()

    def test_client_availability_update_no_client_provided(self):
        with self.assertRaises(ValueError):
            self.app.upload_to_ftp(client=None)

    def test_upload_to_ftp_no_client_provided(self):
        with self.assertRaises(ValueError):
            self.app.upload_to_ftp(client=None)
