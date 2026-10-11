import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from studio.candidate import CandidateError, load_candidate, write_candidate, validate_promotion, PromotionJournal

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        artifact = self.root / 'app.zip'
        artifact.write_bytes(b'original')
        self.data = dict(schema=1, id='example-1', product='example', repository='studio/example',
                         source_sha='a'*40, workflow_run_id=123, workflow_run_attempt=1,
                         version='1.0.1', build_number=123, tooling_revision='b'*40,
                         artifacts=[dict(component='app', channel='direct', path='app.zip',
                                         sha256=hashlib.sha256(b'original').hexdigest(), size=8)],
                         checks={'app': 'passed'}, prerequisites={'signed': 'passed'}, compatibility={'api': 'passed'})
        self.path = self.root / 'candidate.json'

    def test_candidate_is_immutable_and_checks_artifact(self):
        write_candidate(self.path, self.data)
        candidate = load_candidate(self.path)
        self.assertEqual(candidate.data['source_sha'], 'a'*40)
        with self.assertRaises(CandidateError):
            write_candidate(self.path, self.data)
        (self.root / 'app.zip').write_bytes(b'changed')
        with self.assertRaises(CandidateError):
            load_candidate(self.path)

    def test_digest_and_provenance_fail_closed(self):
        write_candidate(self.path, self.data)
        self.path.write_text(self.path.read_text().replace('example-1', 'example-2'))
        with self.assertRaises(CandidateError):
            load_candidate(self.path)
        self.data['source_sha'] = 'main'
        with self.assertRaises(CandidateError):
            write_candidate(self.root / 'bad.json', self.data)

    def test_candidate_product_root_is_validated_and_legacy_defaults_to_repository_root(self):
        write_candidate(self.path, self.data)
        self.assertEqual(load_candidate(self.path).data.get('product_root', '.'), '.')
        self.data['product_root'] = '../landing'
        with self.assertRaises(CandidateError):
            write_candidate(self.root / 'bad-root.json', self.data)

    def test_failed_checks_and_digest_unbound_evidence_block_promotion(self):
        write_candidate(self.path, self.data)
        candidate = load_candidate(self.path)
        policy = {'product': 'example', 'repository': 'studio/example', 'release': {'required_checks': ['app'], 'required_evidence': ['installation']}}
        review = {'schema': 1, 'manifest_sha256': candidate.digest, 'reviewer': 'willie', 'checks': [{'name': 'installation', 'status': 'passed', 'url': 'https://example.com/review'}]}
        validate_promotion(candidate, policy, review)
        for bad in ({**review, 'manifest_sha256': 'c'*64}, {**review, 'checks': []}, {**review, 'reviewer': ''}):
            with self.assertRaises(CandidateError):
                validate_promotion(candidate, policy, bad)
        candidate.data['checks']['app'] = 'failed'
        with self.assertRaises(CandidateError):
            validate_promotion(candidate, policy, review)

    def test_journal_resumes_success_and_preserves_pending_or_failed(self):
        journal = PromotionJournal(self.root / 'journal.json', 'c'*64)
        calls = []
        journal.perform('upload', lambda: calls.append('upload'))
        resumed = PromotionJournal(self.root / 'journal.json', 'c'*64)
        resumed.perform('upload', lambda: calls.append('duplicate'))
        def fail():
            raise RuntimeError('Apple pending')
        with self.assertRaises(RuntimeError):
            resumed.perform('notarize', fail)
        self.assertEqual(calls, ['upload'])
        self.assertEqual(json.loads(journal.path.read_text())['operations']['notarize']['state'], 'failed')
        with self.assertRaises(CandidateError):
            PromotionJournal(journal.path, 'd'*64)

    def test_journal_remote_persistence_runs_after_each_transition(self):
        saved = []
        journal = PromotionJournal(self.root / 'persist.json', 'e'*64, on_save=lambda path: saved.append(json.loads(path.read_text())))
        journal.perform('upload', lambda: {'url': 'https://example.com/build'})
        self.assertEqual([item['operations'].get('upload', {}).get('state') for item in saved], [None, 'running', 'completed'])

    def test_artifact_changed_after_inspection_blocks_promotion(self):
        write_candidate(self.path, self.data)
        candidate = load_candidate(self.path)
        (self.root / 'app.zip').write_bytes(b'changed!')
        policy = {'product': 'example', 'repository': 'studio/example', 'owner_repository': 'studio/example', 'release': {'required_checks': ['app'], 'required_evidence': []}}
        with self.assertRaises(CandidateError):
            validate_promotion(candidate, policy, {'schema': 1, 'manifest_sha256': candidate.digest, 'reviewer': 'willie', 'checks': []})

    def test_supporting_repository_cannot_promote_owner_release(self):
        write_candidate(self.path, self.data)
        candidate = load_candidate(self.path)
        policy = {'product': 'example', 'repository': 'studio/example', 'owner_repository': 'studio/owner', 'release': {'required_checks': ['app'], 'required_evidence': []}}
        with self.assertRaises(CandidateError):
            validate_promotion(candidate, policy, {'schema': 1, 'manifest_sha256': candidate.digest, 'reviewer': 'willie', 'checks': []})

    def test_pending_operation_is_retried_without_losing_completed_operation(self):
        calls = []
        journal = PromotionJournal(self.root / 'pending.json', 'f'*64)
        journal.perform('upload', lambda: calls.append('uploaded'))
        journal.perform('apple', lambda: {'state': 'apple-pending', 'request_id': 'request-1'})
        resumed = PromotionJournal(journal.path, 'f'*64)
        self.assertEqual(resumed.data['operations']['apple']['state'], 'apple-pending')
        resumed.perform('upload', lambda: calls.append('duplicate'))
        resumed.perform('apple', lambda: {'state': 'completed', 'request_id': 'request-1'})
        self.assertEqual(calls, ['uploaded'])
        self.assertEqual(resumed.data['operations']['apple']['state'], 'completed')
