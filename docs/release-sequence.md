# Product candidate and promotion sequence

Studio reviewers must inspect an immutable candidate and submit digest-bound
acceptance evidence before invoking Promote. Product owners must maintain this
sequence whenever they change release ordering.

The product's Mac repository owns the assembled release. Supporting repositories
publish ready records after successful default-branch checks. The shared
implementation captures those exact artifacts during assembly. A notification
starts assembly and never authorizes production.

The following sequence diagram shows candidate creation, human authorization,
dependency promotion, and the separate direct and Store channels.

```mermaid
sequenceDiagram
    actor Engineer
    actor Reviewer
    participant Support as Supporting repository
    participant Product as Product repository
    participant Engine as Pinned release implementation
    participant Stage as Staging providers
    participant Production as Production providers
    participant Downloads as Direct distribution
    participant Apple as App Store Connect
    Engineer->>Support: Push a component change
    Support->>Engine: Validate and build immutable component
    Engine-->>Support: Ready record with source SHA and digests
    Support->>Product: Notify exact ready candidate
    Engineer->>Product: Push application change
    Product->>Engine: Check, build, sign and assemble candidate
    Engine->>Stage: Deploy isolated previews
    Engine-->>Product: Manifest, shipping artifacts and review evidence
    Product-->>Reviewer: Show exact candidate and changes since production
    Reviewer->>Product: Invoke Promote with manifest digest and evidence
    Product->>Engine: Verify source run, bytes and authorization
    Engine->>Production: Apply compatible migrations and promote dependencies
    Production-->>Engine: Confirm artifact identity and required probes
    par Direct channel
        Engine->>Downloads: Publish immutable signed downloads
        Engine->>Downloads: Publish final signed update feed
    and Store channel
        Engine->>Apple: Upload exact approved Store package
        Engine->>Apple: Submit selected build for review with manual release
        Apple-->>Engine: Return review or processing state
        Note over Engine,Apple: Apple-pending is recorded independently
        Apple-->>Engine: Approve selected build
        Engine->>Product: Recheck authorization and supersession
        Engine->>Apple: Request release of the still-authorized build
    end
    Engine->>Production: Publish website artifacts and release metadata
    Engine-->>Product: Persist completed, partial, failed or Apple-pending record
    Note over Engine,Product: Retries use the same manifest and resume recorded operations
```

The shared workflows run in the calling repository. GitHub's
[reusable workflow contract](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
provides that ownership boundary. Every product pins the implementation by
commit SHA, and its launcher also checks the release archive's SHA-256.

A manual invocation records the reviewer who authorized the manifest. It does
not create an independent required-reviewer gate on the organization's current
private-repository plan. The direct release and Apple's review cannot form one
atomic transaction. A later push creates another candidate. A later approved
candidate supersedes an older authorization before any mutable publication.

The implementation rejects missing credentials, failed product checks, missing
updater configuration, changed artifacts, incompatible dependency contracts and
unbound review evidence. Reviewers must record clean installation, supported
hardware launch, data-preserving upgrades and connected acceptance against the
shipping artifact. Staging screenshots supply additional evidence.
