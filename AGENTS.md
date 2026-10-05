# Release engineering contributor rules

Keep product repositories separate. This repository owns lifecycle policy,
release adapters, launcher generation, infrastructure modules, and the Swift
distribution package. Native tools own builds, package caches, and deployments.

Use Conventional Commits with scopes `cli`, `macos`, `distribution`, `workflows`,
`infra`, and `docs`. Use message files and include the reason for decisions.
Do not use git commit -m. Keep origin on SSH.

Use Python 3.11 or newer. Run unittest discovery, launcher tests, workflow
validation, and Swift package tests before publishing. Bound native build
parallelism to two workers. Never print secret values.

All promotion paths must validate immutable candidate provenance and digests.
Do not rebuild during promotion. Preserve failed, partial, and Apple-pending
states. Never weaken acceptance requirements to make a product releasable.

Record implementation and external acceptance separately in docs/rollout.md.
