#!/usr/bin/env python3
"""Prepare a checkout for development.

Every checkout gets the Python environment and the Swift package dependencies
that the tests need. A checkout whose effective committer email is at
@hypertext.studio also gets the commit hooks and pinned formatters, because
installing the formatters needs Studio package access. Any other checkout
skips those steps and the report names each one.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DETECT = object()
STUDIO_EMAIL = re.compile(r"[^@\s]+@hypertext\.studio", re.I | re.ASCII)


def committer_email(root):
    """Return the email Git would record for a commit, or None when it has none."""
    result = subprocess.run(
        ["git", "-C", str(root), "var", "GIT_COMMITTER_IDENT"],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"<([^<>]+)>", result.stdout) if result.returncode == 0 else None
    return match.group(1) if match else None


def is_maintainer(email):
    return bool(email and STUDIO_EMAIL.fullmatch(email))


def venv_python(root):
    return root / ".venv/bin/python"


def steps(root, python=sys.executable):
    """Each step is (name, argv, needs_studio_access)."""
    return [
        ("Python environment", [python, "-m", "venv", str(root / ".venv")], False),
        (
            "Python dependencies",
            [str(venv_python(root)), "-m", "pip", "install", str(root)],
            False,
        ),
        ("Swift package dependencies", ["swift", "package", "resolve"], False),
        (
            "Commit hooks and pinned formatters",
            [python, str(root / ".hypertext/formatting/install.py")],
            True,
        ),
    ]


def run(root=ROOT, email=DETECT, runner=subprocess.run, out=print):
    root = Path(root)
    email = committer_email(root) if email is DETECT else email
    maintainer = is_maintainer(email)
    skipped = []
    for name, argv, needs_studio_access in steps(root):
        if needs_studio_access and not maintainer:
            skipped.append(name)
            continue
        out("Running: " + name)
        if runner(argv, cwd=root).returncode:
            out("Failed: " + name)
            return 1
    if skipped:
        out(
            (
                "Committer " + email + " is outside @hypertext.studio"
                if email
                else "Git has no committer email for this checkout"
            )
            + ", so setup took the contributor path."
        )
        for name in skipped:
            out("Skipped (needs Studio package access): " + name)
        out("Before pushing, run the build and test commands in docs/DEVELOPMENT.md.")
        out("The pull request checks decide formatting and commit messages.")
        out(
            "Maintainers: run git config --local user.email YOUR@hypertext.studio, then run setup again."
        )
    return 0


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    raise SystemExit(run())
