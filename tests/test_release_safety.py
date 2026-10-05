import unittest
from studio.ci import assert_current_authorization, require_completed, CandidateError

class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.candidate=type('Candidate',(),{'digest':'a'*64,'data':{'id':'12-1','build_number':12001}})()
        self.authorizations=[{'candidate_id':'12-1','manifest_sha256':'a'*64,'build_number':12001,'authorization':'authorized'}]
    def test_revocation_supersession_and_digest_replacement_block(self):
        assert_current_authorization(self.candidate,self.authorizations)
        for records in ([{**self.authorizations[0],'authorization':'revoked'}], [{**self.authorizations[0],'manifest_sha256':'b'*64}], self.authorizations+[{'candidate_id':'13-1','manifest_sha256':'b'*64,'build_number':13001,'authorization':'authorized'}]):
            with self.assertRaises(CandidateError): assert_current_authorization(self.candidate,records)
    def test_unfinished_dependency_stops_later_operations(self):
        self.assertEqual(require_completed({'state':'completed'}), {'state':'completed'})
        for state in ['failed','partial','pending','apple-pending']:
            with self.assertRaises(CandidateError): require_completed({'state':state})

    def test_revoking_a_newer_build_cannot_reauthorize_an_old_build(self):
        records=self.authorizations+[{'candidate_id':'13-1','manifest_sha256':'b'*64,'build_number':13001,'authorization':'revoked'}]
        with self.assertRaises(CandidateError): assert_current_authorization(self.candidate,records)
