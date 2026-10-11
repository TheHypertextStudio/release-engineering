import tempfile
import subprocess
import unittest
from pathlib import Path

from studio.candidate import CandidateError
from studio.product_root import repository_relative_root, resolve_product_root, validate_product_root


class ProductRootTests(unittest.TestCase):
    def test_resolves_nested_product_and_keeps_dot_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            for root in (checkout, checkout / 'landing'):
                root.mkdir(exist_ok=True)
                (root / 'studio.yaml').write_text('product: test\n')
                (root / 'studio.lock.json').write_text('{}\n')
            self.assertEqual(resolve_product_root(checkout, 'landing'), checkout / 'landing')
            self.assertEqual(resolve_product_root(checkout), checkout)

    def test_rejects_absolute_traversal_noncanonical_and_control_paths(self):
        for value in ('/tmp/product', '../product', 'a/../b', 'a//b', 'a\\b', 'a\nb', 'C:/product'):
            with self.subTest(value=value), self.assertRaises(CandidateError):
                validate_product_root(value)

    def test_rejects_symlink_directory_and_symlink_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            actual = checkout / 'actual'
            actual.mkdir()
            (actual / 'studio.yaml').write_text('product: test\n')
            (actual / 'studio.lock.json').write_text('{}\n')
            (checkout / 'link').symlink_to(actual, target_is_directory=True)
            with self.assertRaises(CandidateError):
                resolve_product_root(checkout, 'link')
            (checkout / 'product').mkdir()
            (checkout / 'product' / 'studio.yaml').symlink_to(actual / 'studio.yaml')
            (checkout / 'product' / 'studio.lock.json').write_text('{}\n')
            with self.assertRaises(CandidateError):
                resolve_product_root(checkout, 'product')

    def test_requires_both_regular_product_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CandidateError):
                resolve_product_root(temporary)

    def test_repository_identity_is_relative_to_the_checkout_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            landing = checkout / 'landing'
            landing.mkdir()
            subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
            self.assertEqual(repository_relative_root(landing), 'landing')


if __name__ == '__main__':
    unittest.main()
