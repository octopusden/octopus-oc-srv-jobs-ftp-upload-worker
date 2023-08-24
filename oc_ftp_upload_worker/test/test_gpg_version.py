import unittest
import gnupg
from packaging import version


class TestGPG(unittest.TestCase):
    def test_version(self):
        _version_cur = version.parse('.'.join(list(map(lambda x: str(x), gnupg.GPG().version))))
        _version_min = version.parse("2.2.27")

        self.assertTrue(_version_cur >= _version_min)
