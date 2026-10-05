import unittest
from studio.ci import verify_run, promotion_order
from studio.candidate import CandidateError

class CITests(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(repository='Studio/app', source_sha='a'*40, workflow_run_id=12, workflow_run_attempt=2, tooling_revision='b'*40)
        self.run = dict(id=12, run_attempt=2, head_sha='a'*40, head_branch='main', event='push', conclusion='success', repository={'full_name':'Studio/app'}, path='.github/workflows/candidate.yml')
    def test_requires_successful_default_branch_candidate_workflow(self):
        verify_run(self.manifest, self.run, 'main')
        for changed in ({'head_sha':'c'*40}, {'head_branch':'feature'}, {'event':'pull_request'}, {'conclusion':'failure'}, {'path':'.github/workflows/other.yml'}, {'run_attempt':3}):
            with self.subTest(changed=changed), self.assertRaises(CandidateError):
                verify_run(self.manifest, {**self.run, **changed}, 'main')
    def test_dependencies_promote_before_clients_and_site(self):
        components=[{'id':'site','kind':'static-site'}, {'id':'app','kind':'macos'}, {'id':'api','kind':'cloud-run'}, {'id':'db','kind':'cloudflare-worker'}]
        self.assertEqual([c['id'] for c in promotion_order(components)], ['api','db','app','site'])
