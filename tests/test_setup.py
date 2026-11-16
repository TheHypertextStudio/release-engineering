import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "checkout_setup", ROOT / "scripts/setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Recorder:
    def __init__(self, failing=None):
        self.calls = []
        self.failing = failing

    def __call__(self, argv, cwd=None):
        self.calls.append(argv)
        return SimpleNamespace(returncode=1 if argv[0] == self.failing else 0)


def run(email, runner=None):
    runner = runner or Recorder()
    lines = []
    code = setup.run(ROOT, email=email, runner=runner, out=lines.append)
    return code, runner.calls, lines


def formatter_runs(calls):
    return [
        c for c in calls if any(part.endswith("formatting/install.py") for part in c)
    ]


class MaintainerDecisionTests(unittest.TestCase):
    def test_studio_addresses_are_maintainers(self):
        self.assertTrue(setup.is_maintainer("willie@hypertext.studio"))
        self.assertTrue(setup.is_maintainer("Someone@Hypertext.Studio"))

    def test_other_addresses_are_not_maintainers(self):
        for email in (
            "dev@example.com",
            "x@hypertext.studio.evil.com",
            "x@evil-hypertext.studio",
            None,
            "",
        ):
            self.assertFalse(setup.is_maintainer(email), email)

    def test_committer_email_reads_the_identity_git_would_record(self):
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(["git", "init", "--quiet", folder], check=True)
            subprocess.run(
                ["git", "-C", folder, "config", "--local", "user.name", "Dev"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    folder,
                    "config",
                    "--local",
                    "user.email",
                    "dev@example.com",
                ],
                check=True,
            )
            self.assertEqual("dev@example.com", setup.committer_email(folder))


class ContributorPathTests(unittest.TestCase):
    def test_contributor_prepares_the_native_toolchain_and_skips_studio_steps(self):
        code, calls, lines = run("dev@example.com")
        self.assertEqual(0, code)
        self.assertEqual([], formatter_runs(calls))
        self.assertIn(["swift", "package", "resolve"], calls)
        self.assertEqual(3, len(calls))
        self.assertIn(
            "Skipped (needs Studio package access): Commit hooks and pinned formatters",
            lines,
        )

    def test_report_names_the_reason_and_the_next_steps(self):
        _, _, lines = run("dev@example.com")
        text = "\n".join(lines)
        self.assertIn("Committer dev@example.com is outside @hypertext.studio", text)
        self.assertIn("docs/DEVELOPMENT.md", text)
        self.assertIn("pull request checks decide", text)
        self.assertIn("git config --local user.email YOUR@hypertext.studio", text)

    def test_missing_identity_takes_the_contributor_path(self):
        code, calls, lines = run(None)
        self.assertEqual(0, code)
        self.assertEqual([], formatter_runs(calls))
        self.assertIn(
            "Git has no committer email for this checkout, so setup took the contributor path.",
            lines,
        )

    def test_contributor_path_never_asks_for_a_token_or_exception(self):
        _, calls, lines = run("dev@example.com")
        text = " ".join(" ".join(c) for c in calls) + " ".join(lines)
        for forbidden in (
            "NODE_AUTH_TOKEN",
            "HYPERTEXT_ALLOW_NON_STUDIO_EMAIL",
            "gh auth",
            "gcloud",
        ):
            self.assertNotIn(forbidden, text)


class MaintainerPathTests(unittest.TestCase):
    def test_maintainer_also_installs_hooks_and_formatters(self):
        code, calls, lines = run("willie@hypertext.studio")
        self.assertEqual(0, code)
        self.assertEqual(1, len(formatter_runs(calls)))
        self.assertEqual(4, len(calls))
        self.assertFalse([line for line in lines if line.startswith("Skipped")])

    def test_failed_step_stops_setup(self):
        code, calls, lines = run("willie@hypertext.studio", Recorder(failing="swift"))
        self.assertEqual(1, code)
        self.assertEqual([], formatter_runs(calls))
        self.assertIn("Failed: Swift package dependencies", lines)


if __name__ == "__main__":
    unittest.main()
