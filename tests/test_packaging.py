import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_release_builder_exists(self):
        self.assertTrue((ROOT / "scripts/build-release.py").is_file())

    def test_launcher_rejects_modified_cached_application(self):
        script = ROOT / "scripts/render-launcher.py"
        self.assertTrue(script.is_file())
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            launcher = root / "product/run"
            subprocess.run(["python3", str(script), "--version", "v0.1.0", "--sha256", "a" * 64, "--output", str(launcher)], check=True)
            cache = root / "cache/hypertext-studio/release-engineering/v0.1.0"
            cache.mkdir(parents=True)
            (cache / "studio.pyz").write_text("malicious code")
            result = subprocess.run([str(launcher), "doctor"], cwd=root, env={**os.environ, "XDG_CACHE_HOME": str(root / "cache")}, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or corrupt", result.stderr)

    def test_launcher_anchors_root_and_preserves_arguments(self):
        self.assertTrue((ROOT / "scripts/render-launcher.py").is_file())
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            app = root / "engine.py"
            app.write_text("import json,sys; print(json.dumps(sys.argv[1:]))")
            import hashlib
            digest = hashlib.sha256(app.read_bytes()).hexdigest()
            launcher = root / "product with spaces/run"
            subprocess.run(["python3", str(ROOT / "scripts/render-launcher.py"), "--version", "v0.1.0", "--sha256", digest, "--output", str(launcher)], check=True)
            cache = root / "cache/hypertext-studio/release-engineering/v0.1.0"
            cache.mkdir(parents=True)
            (cache / "studio.pyz").write_bytes(app.read_bytes())
            import os, json
            result = subprocess.run([str(launcher), "release", "inspect", "--candidate", "a value"], cwd=root, env={**os.environ, "XDG_CACHE_HOME": str(root / "cache")}, text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), ["--root", str(launcher.parent), "release", "inspect", "--candidate", "a value"])


if __name__ == "__main__":
    unittest.main()
