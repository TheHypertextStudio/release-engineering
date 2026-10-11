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

Company website configuration and Mintlify standardization began October 10.
The [website status record](website-standardization.md) contains scope, source
reconciliation, provider access gaps, and local implementation evidence. The
[website contract](websites.md) extends the existing lifecycle with prebuilt
Workers site artifacts. No company website cutover or legacy hosting retirement
has been accepted. Mintlify remains the product documentation platform.

Packaged Astro and Next.js/OpenNext fixtures now pass local workerd checks
for release identity, HTML/modules/assets, 404s, and cookie-dependent rendering.
Their native bundle is produced before archiving; local staging and production
extract the same shipping archive without rebuilding. Hosted runtime CI and
real provider preview/promotion/recovery are tracked separately. This does not
establish product compatibility, authenticated acceptance, or a hosting cutover.

Two isolated framework previews now pass HTTPS metadata, HTML/assets, and 404
checks on Cloudflare. The Next.js fixture also passes viewer isolation and
private cache header checks. Initial failures are retained in
[website-runtime-acceptance.json](website-runtime-acceptance.json), followed by
successful reconciliation of the same provider deployments. The same retained
archives also pass the production overlay on two isolated test Workers, without
rebuilding. HTTPS checks compare Astro HTML and static assets with archive
bytes, verify six Next.js modules per environment, reject a wrong release
digest, and exercise private viewer rendering. Read-only provider deployment
records prove that repeated operator acceptance did not redeploy either test
Worker. These operator fixtures do not provide product promotion authorization.
Complete release/routing recovery and hosted product promotion remain open.

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

## Retained OpenNext cache and Images contract

The shared Workers frontend adapter now accepts native Images bindings and an
explicit retained OpenNext R2 cache directory. It derives keys from native
OpenNext 1.20.10's published implementation, validates both environments and
preview bucket aliases, and uploads/reads exact reviewed bytes through pinned
Wrangler before Worker deployment. Cache operation receipts bind the artifact
and selected provider identities, survive partial failures in the production
promotion journal, and resume completed object operations without re-uploading.
No OpenNext source build, dependency resolution, bucket provisioning, secret
creation, provider writes or production release was performed for this change.

Implementation verification and external acceptance are distinct: synthetic
fault cases cover retries, readback mismatch, identity drift and isolation;
pinned Wrangler local R2 and Images packaging plus real framework archive
acceptance cover native behavior. Remote cache permissions and seeding,
Images transformations, real product/provider behavior, and rollback remain
required before production adoption. Context7 documentation retrieval was
attempted and blocked by its monthly quota; the exact installed native schemas
and source supplied the command and serialization contract.

Fresh verification passed all 180 Python tests with both
`STUDIO_NATIVE_WORKERS_TESTS=1` and `STUDIO_FRAMEWORK_WORKERS_TESTS=1`, including
the native R2 round-trip, detached Next cache delivery and authorization fault
cases. Four Swift tests, actionlint, runtime assembly and `git diff --check`
also passed. The initial new-checkout baseline failed only because the native
Sparkle tools had not yet been resolved; resolving the existing Swift package
removed that prerequisite failure. Parallel product-root changes briefly
exposed four fixture/compatibility failures; those were fixed before the final
complete run.

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
