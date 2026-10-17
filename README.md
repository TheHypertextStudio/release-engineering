# Studio release engineering

Native product engineers and coding agents declare product facts in `studio.yaml`
and use the pinned lifecycle implementation. Product reviewers must inspect the
shipping candidate and invoke Promote with its manifest digest and acceptance
evidence.

Native products using this lifecycle expose the following commands:

```sh
./run setup
./run doctor
./run dev
./run check
./run build
./run provision plan --env staging
./run provision apply --env staging
./run release inspect --candidate 123456789-1
./run release promote --candidate 123456789-1 --evidence review.json
```

Product repositories own source, tests, infrastructure bindings, GitHub runs,
review artifacts, authorization and release history. This repository owns native
lifecycle dispatch, candidate assembly, promotion, signing, notarization,
packaging, update feeds, hosting, provisioning modules and distribution SDKs.
Products must not reimplement those operations.

[The release sequence](docs/release-sequence.md) explains how a push becomes a
candidate and how approved dependencies precede client publication.
Company websites use pinned native pnpm, framework adapters and Wrangler commands
in their own repositories, with conventional GitHub Actions and environment
approvals. They do not use this engine for frontend candidate, cache, recovery
or Mintlify inspection operations. Mintlify keeps its Git publisher and renderer.
[The macOS contract](docs/macos.md) specifies signing, Sparkle and Store profiles.
[The rollout record](docs/rollout.md) distinguishes tested code from credentialed
product acceptance. No unsigned local build satisfies a signed-release gate.

The Swift package provides `Distribution` for build information and Store
channel behavior. `DirectDistribution` adds Sparkle and shared update commands.
Store targets link only `Distribution`. Curfew remains direct-only.

A product pins one shared release through `studio.lock.json`, `run`, reusable
workflow references, the Swift package revision and infrastructure modules.
The scheduled shared pin updater changes those references in one reviewable
branch. It does not approve production or bypass a repository's integration
policy.

Native Xcode, SwiftPM, pnpm, Turbo, Gradle, Terraform and provider CLIs retain
their build and cache responsibilities. Existing `bootstrap worktree prepare`
continues to prepare worktrees. Its v0.1.0 binaries are mirrored unchanged in
this repository's v0.1.0 release so PR tokens need no private repository access.
Generated bootstrap launchers use the public mirror and retain the original
platform checksums. Bootstrap continues to own its implementation and cache
behavior. Infrastructure provisioning remains explicit.

The current adapters cover native macOS apps, Cloud Run services, Cloudflare
Workers and npm packages. Website hosting uses native Workers configuration and deployment in
its frontend repository; retired hosting profiles are rejected before provider commands. Android and iOS distribution
adapters require their own acceptance before adoption.

Contributors run the following checks with two native workers:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
swift test --jobs 2
.venv/bin/python -m unittest discover -s tests -v
actionlint
terraform -chdir=infra/modules/product init -backend=false
terraform -chdir=infra/modules/product validate
.venv/bin/python scripts/build-release.py
```

The CLI requires Python 3.11 or newer. The release zip application includes the
pure Python YAML reader. It does not depend on a globally installed Python
package. Shared native tools also verify their published checksums.

## Shared Turbo cache authentication

Studio Turbo consumers use [the shared authentication action](docs/remote-cache.md) pinned to a
reviewed commit at `TheHypertextStudio/release-engineering/actions/turbo-cache@<revision>`.
It mints an audience-bound Google ID token for each repository's dedicated cache identity, keeps
untrusted refs local-only, and preserves client artifact signatures. This small composite action
is independent of candidate/promotion workflows and never deploys a website.
