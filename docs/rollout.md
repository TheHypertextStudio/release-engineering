# Curfew release adoption

Studio release maintainers must use this record to distinguish implementation
checks from shipping acceptance. Update it whenever Curfew passes a release
gate or a release operation changes.

## Decision

On October 5, 2026, we constrained the first rollout to shared release
engineering and Curfew direct distribution. The other product migrations
remain local, uncommitted work. They have not passed adoption acceptance.
Curfew cannot use the Mac App Store adapter with its unsandboxed profile.

We chose GitHub reusable workflows and a checksum-pinned zip application.
Product repositories keep native build facts and product tests. They cannot
copy signing, packaging, notarization or deployment implementations.
The [sequence diagram](release-sequence.md) records the approval and channel
boundaries. Existing bootstrap tooling continues to prepare worktrees.

## Evidence

Local Python behavior tests cover immutable manifests, failed prerequisites,
exact review digests, revoked and superseded approvals, deployment probes,
interrupted promotion journals, duplicate notifications, store isolation and
conditional update withdrawal. Four Swift SDK tests pass. The real Sparkle
2.10.0 tools have been exercised against a local signed fixture. This fixture
does not prove Developer ID distribution or previous-client acceptance.

Apple issued a Developer ID Application certificate for Hypertext Studio, LLC,
team T95VDD3A4W. Its certificate matches the protected private key. Apple
accepted the new Developer API key for a notarization history request.
Credential bytes remain outside source control.

The release projects are `hypertext-studio-releases` for hosting and
`hypertext-curfew-release` for Curfew workload identities and secrets.
Product access uses a separate namespace and service accounts.

The pin updater uses a repository-scoped SSH deploy key in Secret Manager.
A separate workload identity can read that key. Candidate and promotion
identities cannot read it. The updater creates a reviewable branch and never
approves or promotes its own changes. A committed version override is consumed
when that version reaches production, so the next push derives a new version.

An independent review found that repository-wide impersonation grants defeated
phase isolation. The live grants now bind each account to a fixed provider
phase. The module maps that phase separately from repository identity. Review
also corrected target entitlement overrides and stale Swift resolution pins.
The actual Developer ID archive is blocked by missing App Groups profiles.
Apple Developer Support accepted case 102987990794 on October 6, 2026 to
investigate the Curfew identifiers previously provisioned under Personal Team
39AB9DY3K8. Registration and company-team profile creation await Apple's
guidance. Do not remove the existing identifiers or change Curfew's bundle IDs
while that case is open.

On October 6, a local Curfew production plan could not read its GCS state
because Google authentication returned `invalid_rapt`. The shared provision
command previously returned success after Terraform failed. The runtime now
propagates that failure. This attempt does not establish whether production
infrastructure has drifted. A fresh plan must run after Google authentication
is restored.

GitHub Actions was disabled on `TheHypertextStudio/release-engineering` until
October 6, despite the repository API reporting Actions as enabled. A maintainer
enabled it on the Actions page. The v0.1.4 tag predates that change, so its
automatic tag workflow did not run. Maintainers ran CI on the exact tag and
published its verified archive manually. Automatic push and pull-request
triggers passed hosted smoke runs on [PR #2](https://github.com/TheHypertextStudio/release-engineering/pull/2).

The first Curfew credential diagnostic only inspected GitHub secrets. It
reported Apple credentials as absent while Curfew declared Secret Manager
bindings. The shared diagnostic now checks those bindings with the product's
staging workload identity. It records unavailable bindings without printing
secret values. A legacy GitHub secret cannot mask an unavailable declared
binding. The candidate identity also admits the exact pinned diagnostic
workflow when a maintainer invokes it on the default branch. Pull-request refs
cannot assume that identity. A fresh diagnostic after Curfew adopts the new pin
must verify the live credentials before they count as ready.

## Open acceptance

Company websites use their native framework, pnpm and Wrangler commands.
Website deployment is outside this lifecycle engine. Mintlify remains the
product documentation publisher. No company website cutover or legacy hosting
retirement has been accepted by this cleanup.

The first signed Curfew candidate must pass hosted validation, notarization,
installation and launch on supported Macs. The reviewer must inspect its
shipping bytes and record licensed delivery and update evidence. A staging or
unsigned local app cannot replace this review.

The previous release must update through Sparkle without losing settings.
If no previous compatible direct release exists, record that fact and validate
an installed baseline before approving the first public feed.

Curfew supporting services and the website still require real staging and
production bindings before joining assembled candidates. Their old mechanisms
remain preserved. LogDate connected acceptance and migration gates remain
unchanged. Docket, native LogDate, Genny, Plasma and Storyloom require separate
adoption acceptance before their old implementations can be deleted.

## Recovery

The manual promotion workflow accepts `withdraw` for an exact candidate and
manifest digest. Withdrawal revokes its authorization before removing that
build from the current update feed. It keeps immutable download assets and
release history. Generation preconditions stop recovery from overwriting a
concurrent feed or another candidate's current metadata.

Publish a corrected client with a higher build number. Service recovery must
select a compatible previous artifact. Store releases remain subject to Apple
approval and a fresh authorization check.

## Lifecycle workflow compatibility and credential precedence

Candidate and validation workflows now read and validate the declared exact
Node version before selecting the native runtime. Promotion selects the reviewed
candidate's Node pin after source, policy, lock, root and engine preflight. These
checks run with the installed dispatcher in Python isolated mode, before
credential preparation; the exact candidate runtime is restored only for the
final promotion command. Default-root v0.1.7 candidates remain supported without
calling newer helper modules or flags after that boundary. Legacy candidates
cannot claim a newly scoped product root.

Private registry setup receives the job token before dependency preparation.
Candidate signing credentials are prepared before diagnostics and assembly.
Declared managed credentials remain authoritative across later workflow steps;
repository secrets are fallbacks only when no managed binding is declared.
Denied or empty managed bindings fail before stale values can be exported.

Verification includes an offline retained v0.1.7 runtime boundary, source-package
shadowing protection, managed-binding denial and credential export precedence.
The complete 199-test Python suite passes with both native Workers suites enabled
on Node 24.20.0, together with four Swift tests, actionlint, runtime assembly and
`git diff --check`. This includes the independently committed lazy variable API
fix. Hosted workflow execution, real signing, private package delivery and
provider promotion remain external acceptance gates; no push or release was
performed for these changes.

## Website machinery removal (2026-10-11)

The frontend Workers deployment/caching/recovery adapter, Mintlify inspection
client, website fixtures and framework CI job have been removed. Websites now
use official OpenNext/Wrangler output and provider version operations directly;
this repository does not replace them with another archive format, REST client,
cache transport, probe protocol or activation flag. Detailed previous website
experiments remain in Git history and ignored migration acceptance evidence.
Published runtime tags and their archived bytes are unchanged.

Existing native/package candidate and promotion APIs, source/root/lock guards,
credential precedence, signing, macOS SDK, Cloud Run and standalone Worker
adapters, legacy Pages/Vercel adapters and package helpers are preserved. The
website-only identity protocol and its cache/recovery journals have no remaining
execution path. The separate unmerged S3 transport draft is not integrated or
published by this cleanup.

Static source/workflow checks and mandatory commit hooks are recorded separately.
No new/manual tests, dependency installation, provider operation, product
activation, push, PR, runtime publication or website cutover is claimed. The
historical verification counts above describe the earlier source, not this
reduced suite. Existing ordinary CI and release gates remain in their workflows.

## Workers hosting standard and shared cache authentication

Current source removes Pages and Vercel publication commands, Vercel prebuilt assembly, and
credential forwarding. Unsupported website profiles fail before native provider commands;
retained static artifacts and published legacy runtime bytes remain readable. Website frontends
own native Workers configuration/deployment. Cloud Run, standalone Worker, native app, package,
signing, immutable provenance and installed-client compatibility paths retain their contracts.

The independent `actions/turbo-cache` composite supplies short-lived Google authentication to
Studio main workflows. Callers retain local-only defaults, conditionally expose the HMAC key and
use a dedicated exact-repository WIF identity. The action introduces no cache server, static token,
provider permissions or new release workflow. Local checks establish implementation behavior;
consumer integration and credentialed cache acceptance remain separate external observations.
