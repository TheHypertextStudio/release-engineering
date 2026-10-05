import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from studio.config import Config
from studio import ci

class ReconciliationTests(unittest.TestCase):
    def test_pending_store_dispatch_restores_candidate_runtime_without_new_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            configuration=Config(root,{'repository':'Studio/app','product':'app'})
            release={'tag_name':'promotion-12-1','body':'{"candidate_id":"12-1","manifest_sha256":"'+('a'*64)+'","authorization":"authorized"}','assets':[]}
            def restore(repo,release,name,directory):
                (directory/name).write_text('{"operations":{"app:store-review":{"state":"apple-pending"}}}')
                return True
            candidate=type('Candidate',(),{'digest':'a'*64,'data':{'id':'12-1','tooling_revision':'b'*40}})()
            with patch('studio.ci.api',return_value=[release]),patch('studio.ci._restore_asset',side_effect=restore),patch('studio.ci.download',return_value=candidate),patch('studio.ci.execute') as dispatch:
                ci.reconcile(configuration)
                command=dispatch.call_args.args[0]
                self.assertIn('release_action=reconcile-one',command)
                self.assertIn('manifest_sha256='+'a'*64,command)
                self.assertIn('candidate_id=12-1',command)
