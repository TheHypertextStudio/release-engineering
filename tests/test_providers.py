import importlib
import pathlib
import tempfile
import unittest


class ProviderTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("studio.providers")
        except ImportError:
            self.fail("Shared provider adapter is not implemented")

    def test_cloud_run_requires_immutable_image_digest(self):
        module = self.module()
        with self.assertRaises(ValueError):
            module.cloud_run_plan({"id": "api", "deploy": {"project": "project", "region": "us-central1", "service": "api"}}, {"image": "registry/api:latest"}, "production")

    def test_cloud_run_deploys_then_probes_before_moving_traffic(self):
        module = self.module()
        plan = module.cloud_run_plan({"id": "api", "deploy": {"project": "project", "region": "us-central1", "service": "api", "health_path": "/health"}}, {"image": "registry/api@sha256:" + "a" * 64, "revision": "api-candidate-42"}, "production")
        self.assertIn("--no-traffic", plan[0])
        self.assertIn("registry/api@sha256:" + "a" * 64, plan[0])
        self.assertIn("update-traffic", plan[-1])
        self.assertIn("api-candidate-42=100", plan[-1])

    def test_worker_uses_prebuilt_code_without_bundling(self):
        module = self.module()
        plan = module.worker_plan({"id": "sync", "deploy": {"config": "wrangler.jsonc", "environments": {"production": "production"}}}, {"entrypoint": "worker/index.js"}, "production")
        self.assertIn("--no-bundle", plan)
        self.assertIn("worker/index.js", plan)

    def test_hosting_refuses_overwriting_immutable_asset(self):
        module = self.module()
        command = module.hosting_upload_command("bucket", pathlib.Path("App.dmg"), "curfew/candidates/id/App.dmg", immutable=True)
        self.assertIn("--if-generation-match=0", command)

    def test_probe_fails_for_non_https_production_urls(self):
        module = self.module()
        with self.assertRaises(ValueError):
            module.probe("http://example.com/health")
