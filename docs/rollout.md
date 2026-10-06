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

## Open acceptance

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
