#!/usr/bin/env python3
# should be three keypairs:
# company
# client
# client expired
# passphrase for all keys: testkey

import pkg_resources
import os
import base64

class TestKeys():
    def get_key(self, key):
        if hasattr(self, key):
            return getattr(self, key)

        if not any([key.endswith("_priv"), key.endswith("_pub")]):
            # both private and public keys
            setattr(self, key, b'\n\n'.join([self.get_key(f"{key}_priv"), self.get_key(f"{key}_pub")]))
            return getattr(self, key)


        _key_file = pkg_resources.resource_filename("oc_ftp_upload_worker.test", os.path.join("resources", "gpg_keys", f"test_{key}.asc"))

        with open(_key_file, mode='rb') as _x:
            setattr(self, key, base64.b64decode(_x.read()))

        return getattr(self, key)
