import os
import unittest
from unittest.mock import patch

from ..fs_clients import get_clients_repo_url


class ClientsRepoUrlTestSuite(unittest.TestCase):

    @patch.dict(os.environ, {"SVN_URL": "https://svn.example.com/svn", "SVN_CLIENTS_REPO": "test_02"}, clear=True)
    def test_repo_is_appended_to_svn_url(self):
        self.assertEqual("https://svn.example.com/svn/test_02", get_clients_repo_url())

    @patch.dict(os.environ, {"SVN_URL": "https://svn.example.com/svn/", "SVN_CLIENTS_REPO": "/test_02/"}, clear=True)
    def test_slashes_are_not_doubled(self):
        self.assertEqual("https://svn.example.com/svn/test_02", get_clients_repo_url())

    @patch.dict(os.environ, {"SVN_URL": "https://svn.example.com/svn"}, clear=True)
    def test_missing_repo_gives_none(self):
        self.assertIsNone(get_clients_repo_url())

    @patch.dict(os.environ, {"SVN_CLIENTS_REPO": "test_02"}, clear=True)
    def test_missing_svn_url_gives_none(self):
        self.assertIsNone(get_clients_repo_url())
