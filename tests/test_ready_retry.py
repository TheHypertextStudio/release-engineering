import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import ci


class ReadyRetryTests(unittest.TestCase):
    def test_duplicate_notification_preserves_the_original_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'candidate'
            directory.mkdir()
            manifest = directory / 'candidate.json'
            manifest.write_text('{}')
            candidate = type('Candidate', (), {'path': manifest, 'digest': 'a' * 64,
                'data': {'id': '12-1', 'source_sha': 'b' * 40}})()
            release = {'tag_name': 'component-ready-12-1', 'body': 'Manifest SHA-256: ' + 'a' * 64,
                       'assets': [{'name': 'candidate-12-1.zip'}]}
            with patch('studio.ci.api', return_value=[release]), patch('studio.ci.execute') as execute:
                ci.record_ready({'repository': 'Studio/app', 'owner_repository': 'Studio/app'}, candidate)
                ci.record_ready({'repository': 'Studio/app', 'owner_repository': 'Studio/app'}, candidate)
                execute.assert_not_called()
