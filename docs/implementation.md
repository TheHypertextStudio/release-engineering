# Studio lifecycle implementation

Studio engineers must use the shared lifecycle to prepare and release each
product. Product repositories retain source, runs, artifacts, and approvals.

## Delivery sequence

- [ ] Implement the declarative contract, native command dispatcher, immutable
  candidate format, versioning, provenance checks, and resumable promotion state.
- [ ] Implement the shared macOS signing, notarization, Sparkle and store adapters,
  plus the Swift distribution package.
- [ ] Implement reusable validation, candidate, promotion and store reconciliation
  workflows, the checksum-pinned launcher, and infrastructure modules.
- [ ] Adopt fresh Curfew, Docket, LogDate, Genny, Plasma and Storyloom checkouts.
- [ ] Adopt supporting service and website declarations and compatibility gates.
- [ ] Verify local behavioral suites and hosted workflows, then record signed
  installation and updater acceptance without weakening existing product gates.

## Runtime contract

The shared command is `python -m studio`. `studio.yaml` uses YAML with schema 1.
Commands run native tools using argument arrays and explicit working directories.
The launcher downloads an immutable, SHA-256-verified Python zip application;
Python 3.11 or newer is the only launcher runtime. Existing worktree preparation
continues through the repository's `bootstrap worktree prepare` command.
Provisioning requires an explicit environment and saved Terraform plan for
apply. Terraform init, plan, and apply failures must return a nonzero lifecycle
status; a failed backend authentication cannot be reported as a clean plan.

The configuration defines product, repository, owner_repository, toolchain,
components and release. A component declares id, kind, path, checks, optional
dev, and its native build inputs. Component kinds are macos, swiftpm, pnpm,
gradle, cloud-run, cloudflare-worker, and static-site. Build and release policy
remain in this repository. Product-specific checks may execute native test
commands; release hooks and shell snippets are prohibited.

Repositories with multiple development targets may set
`development.component` to a declared component ID. This selects the default
target for `./run dev`; an explicit `--component` takes precedence. The selector
does not change which components `setup`, `check`, or `build` process. Without
the declaration, `dev` keeps its existing all-component default.

Candidates use schema 1, id, product, repository, source_sha, workflow_run_id,
workflow_run_attempt, version, build_number, tooling_revision, artifacts,
checks, prerequisites and compatibility. An artifact records component,
channel, relative path, sha256 and size. A failed check or unmet prerequisite
blocks promotion. Candidate approval records the manifest SHA-256 and reviewer.
Promotion persists each successful operation before advancing and never rebuilds.

Release review evidence is attached to the candidate rather than invented by
CI. Product declarations list required evidence. Manual promotion must bind
review evidence to the candidate digest. No release is accepted merely because
its unsigned build compiled.
