# Development

## Quickstart

These commands build and test a fresh clone with no accounts, sign-ins or
package tokens.

Install Python 3.11 or newer (the shared workflows use 3.14.5) and a Swift 6.0
or newer toolchain from Xcode on macOS 13 or newer. The native Sparkle test
generates an Ed25519 key, which the LibreSSL that ships with macOS cannot do, so
also install OpenSSL 3 or newer (`brew install openssl`) and put its `openssl` first on
`PATH`. Then, from the repository root:

```sh
python3 scripts/setup.py
swift build --jobs 2
swift test --jobs 2
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/build-release.py
```

`scripts/setup.py` creates `.venv`, installs the Python package into it and
resolves the Swift package dependencies that the native Sparkle test reads. It
skips the maintainers' commit hooks and formatters and prints each step it
skipped. The pull request checks decide formatting and commit messages for
outside contributions.

## Maintainers

Setup decides by the effective Git committer email. A maintainer runs
`git config --local user.email YOU@hypertext.studio` and then
`python3 scripts/setup.py` again. Setup then also installs the commit hooks and
the pinned formatters, which download `@thehypertextstudio/format-config` from
GitHub Packages with the maintainer's GitHub sign-in.
