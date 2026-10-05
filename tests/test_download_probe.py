import hashlib
import io
import unittest
from unittest.mock import patch
from studio.providers import verify_download

class PublicDownloadTests(unittest.TestCase):
    def test_download_must_contain_the_reviewed_bytes(self):
        digest=hashlib.sha256(b'reviewed shipping bytes').hexdigest()
        with patch('studio.providers.urllib.request.urlopen',return_value=io.BytesIO(b'reviewed shipping bytes')):
            self.assertEqual(verify_download('bucket','curfew/releases/1-1/App.zip',digest)['status'],'passed')
        with patch('studio.providers.urllib.request.urlopen',return_value=io.BytesIO(b'other bytes')):
            with self.assertRaises(ValueError): verify_download('bucket','curfew/releases/1-1/App.zip',digest)
