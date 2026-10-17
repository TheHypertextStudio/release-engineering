# Shared Studio Turbo cache authentication

Studio Turbo repositories share the private Workers/R2 cache at
`https://turbo-cache.hypertext.studio`. Each repository gets its own artifact namespace, Google
cache-client service-account numeric subject, exact-main WIF binding and client HMAC key. The
cache Worker enforces that identity-to-namespace relationship. Never reuse another product's
identity or signing key to enable a new consumer.

Use this composite action independently of the release engine. Pin a reviewed commit, grant only
`contents: read` and `id-token: write` to the cacheable job, and keep untrusted refs local-only:

```yaml
permissions:
  contents: read
  id-token: write
env:
  TURBO_CACHE: local:rw
  TURBO_REMOTE_CACHE_SIGNATURE_KEY: >-
    ${{ github.repository_owner == 'TheHypertextStudio' &&
    github.ref == 'refs/heads/main' &&
    (github.event_name == 'push' || github.event_name == 'workflow_dispatch') &&
    startsWith(github.workflow_ref, format('{0}/.github/workflows/', github.repository)) &&
    endsWith(github.workflow_ref, '@refs/heads/main') &&
    secrets.TURBO_REMOTE_CACHE_SIGNATURE_KEY || '' }}
steps:
  - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262
  # Prepare the repository's ordinary Node/pnpm dependencies first.
  - uses: TheHypertextStudio/release-engineering/actions/turbo-cache@<reviewed-release-engineering-revision>
    with:
      workload-identity-provider: ${{ vars.GCP_TURBO_CACHE_WIF_PROVIDER }}
      service-account: ${{ vars.GCP_TURBO_CACHE_SERVICE_ACCOUNT }}
      api: ${{ vars.TURBO_API }}
      team: ${{ vars.TURBO_TEAM }}
  - run: pnpm turbo run build
```

Put authentication immediately before the cacheable Turbo invocation, after package installation.
Use the repository's actual targeted task rather than an unnecessary full build. Grant package
read permissions separately when that repository installs private packages. The cache provider
variable is dedicated: **`GCP_TURBO_CACHE_WIF_PROVIDER`**, preserving existing application-deployment
WIF settings. `TURBO_API` is the endpoint audience; `TURBO_TEAM` must match the repository's assigned
namespace. Keep `remoteCache.signature: true` and the Studio endpoint/namespace in native Turbo
configuration so local runs cannot silently choose another vendor's cache.

The action requires the Studio owner, main, a main workflow from the same repository, a push or
manual dispatch, all configured inputs and the client HMAC key. Missing settings/untrusted refs
receive no token and retain the caller's local-only cache. Trusted main inputs are shape-validated
before minting the token. Provider IAM must enforce the narrow exact-repository main subject too;
GitHub expression checks alone are not the security boundary. The action does not provision IAM,
rotate signing keys, deploy Workers or introduce a static cache token. ID tokens are masked,
short-lived and audience-bound; no credential file is created.

For setup, provider checks, local authentication, costs, limits and actual remote-HIT acceptance,
use the Hypertext Studio plugin's `remote-cache-doctor` skill and its bundled runbook. A green job
or healthy endpoint is not proof of a cache hit. The storage goal is zero incremental cost within
shared Workers/R2 allowances, with zero R2 egress charges and native 14-day cache expiration.
