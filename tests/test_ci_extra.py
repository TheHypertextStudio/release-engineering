import unittest
from studio.ci import candidate_build_number
from studio.versioning import next_version

class PolicyTests(unittest.TestCase):
    def test_every_push_has_a_candidate_version(self):
        self.assertEqual(next_version('1.2.3',['chore(dx): Adopt the shared lifecycle']), '1.2.4')
    def test_rerun_build_number_advances_and_next_push_stays_higher(self):
        first=candidate_build_number(10000,15,1)
        self.assertLess(first,candidate_build_number(10000,15,2))
        self.assertLess(candidate_build_number(10000,15,999),candidate_build_number(10000,16,1))
        with self.assertRaises(ValueError): candidate_build_number(0,1,1000)
