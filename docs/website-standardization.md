# Company website standardization status

Observed October 10, 2026. This record separates inspected source, local implementation, and provider acceptance. The machine-readable inventory is [website-standardization-inventory.json](website-standardization-inventory.json).

## Scope and source

| Surface | Repository and directory | Standard |
| --- | --- | --- |
| Studio | `TheHypertextStudio/website`, root | Astro, Workers frontend |
| Curfew marketing | `TheHypertextStudio/curfew`, `landing/` | Astro, Workers frontend |
| Curfew docs | `TheHypertextStudio/curfew`, `docs/` | Mintlify, shared configuration/setup/authoring |
| LogDate | `WillieCubed/logdate-web`, `apps/web/` | Existing Next.js, OpenNext on Workers |
| Docket | `TheHypertextStudio/athena-web`, `apps/web/` | Existing Next.js, OpenNext on Workers |
| Docket docs | `TheHypertextStudio/athena-web`, `apps/docs/` | Mintlify, shared configuration/setup/authoring |

Other companies, native apps, internal administration, and backend migrations are outside this work. Marketing routes already inside Next.js remain there.

Curfew's current source uses `landing/` and `docs/`. Its divergent primary checkout remains untouched. Live inspection confirms Pages still serves `landing/docs.html` at `/docs`; that HTML has no Mintlify assets. The existing Curfew Mintlify project has no Git connection, its initial update failed, and its `/docs` upstream returns 404. Keep the public guide until the existing project publishes the validated source and the proxy passes acceptance.

## Provider access and recovery

- Willie authorized the `cf` device flow. The named `hypertext-studio` profile now exposes Studio account `2500680a3b2b0fe6a011c1c25fed5008`; use `--profile hypertext-studio` explicitly. Read-only inventory captured both Pages projects, production recovery deployments, the five supporting Workers' active/previous versions, D1 metadata, active-version binding names/types, and frontend CNAMEs. The live webmention Worker uses the existing Studio D1 database; Micropub alone has the GitHub write token binding. No provider resource or production route has changed.
- Vercel access is available in `williecubed-projects`. LogDate's canonical origin is `https://logdate.app`; Docket's is `https://clearthedocket.com`. The inventory records project IDs and currently observed production deployment IDs as recovery targets.
- Mintlify dashboard access is available in `hypertextstudio`. Docket publishes `TheHypertextStudio/athena-web`, `apps/docs`, `main`; its latest successful docs update is source `0aadf2bd2669119ee8f99a1474e7fbac85273f57`. Curfew has an existing unconnected project. Hosted previews are unavailable because the Pro trial ended; retain pinned local previews. Contributor roles and API entitlement remain separate checks.
- Docket's active `.mintlify.dev/docs` origin and the dashboard's `.mintlify.site/docs` origin both serve its docs. Public `clearthedocket.com/docs` currently emits canonical tags for `docket.hypertext.studio/docs`; the latter redirects back to the canonical application origin. Reconcile the Mintlify site address during adoption without changing public paths. Do not replace the proven `.dev` proxy merely because the dashboard displays another hostname.
- Anonymous home/docs requests succeeded for all six surfaces. Authenticated journeys, provider errors/latency/usage, complete route/authentication contracts, live route/custom-domain capture, and isolated provider previews remain outstanding.

## Local implementation evidence

- The Workers site adapter accepts prebuilt modules/assets, frozen configuration, explicit account/environment bindings, and full release metadata. Website-only assembly stages and promotes the same archive after the mutable build directory is removed.
- Fresh review found unbounded Wrangler file inputs and shared production resource identities. Regression tests failed before the fixes. Supported file inputs now remain within the archive, and preview resource identities must differ from production. The pinned Wrangler dry run preserves reviewed module/chunk bytes and excludes outside files. All 117 Python checks, four Swift tests, workflow lint, and runtime assembly pass locally. Hosted CI passes; actual framework/provider acceptance remains open.
- The native package profile now uses `pnpm pack --pack-destination`; pnpm 11.9.0 rejects the previous `--outdir` argument.
- Shared Mintlify tooling compiles and installs as a packed package in a clean Node 24.20.0/pnpm 11.9.0 consumer checkout. Product configuration fixtures preserve every Curfew and Docket field. Native validators run against actual content.
- Docket's 52-page docs pass native build validation, links/anchors/redirects/snippets, and accessibility. Curfew's initial checks found two incorrect prefix links and insufficient primary-color contrast. The links are corrected; Willie approved `#b25e30` for docs text/controls while preserving marketing branding.

- External Mintlify observation now has a `docs inspect` CLI path: it verifies GitHub checks on the reviewed SHA, branch ancestry, and provider deployment/project/commit/ref identity; it records queued, failed, and successful states separately from Workers candidates. All 15 focused transport/validation tests and the full 132-test Python suite pass locally; the native GitHub client also read Docket's actual source ancestry and successful docs check runs. Live REST API acceptance remains unverified and requires existing eligible access; no subscription upgrade or admin key was created.

See [websites.md](websites.md) for the adapter and publishing contracts. Local checks do not establish shared package registry availability, live preview/production acceptance, completed rollout observation, or legacy hosting retirement.

### Framework runtime acceptance

Real Astro 7.2.9 and Next.js 16.3.8/OpenNext 1.20.10 shipping artifacts now pass
local workerd acceptance on pinned Wrangler 4.148.0. The first runtime run
observed both missing metadata routes and incorrect viewer rendering; the
completed fixture run passes both archive/environment and viewer-isolation
tests. It covers real HTML, modules, static assets, 404s, all release identity
fields, and unchanged archived bytes across staging/production extraction.
The CI job runs this without provider credentials.

Packaging rejected OpenNext's intermediate dependency symlinks as designed.
Candidate creation now performs the final native Wrangler bundle before
archiving. Promotion does not rebuild or copy a Node dependency tree.

Current OpenNext's declared Next.js peer range excludes LogDate's existing
16.2.4 pin. The fixture uses a supported pair without changing product sources.
Task 7 requires separate dependency preparation and per-product compatibility
evidence. Local fixture success alone does not prove application caching,
authentication, backend behavior, real Cloudflare preview/promotion, or
release/route recovery.

### Isolated provider preview acceptance

The [provider record](website-runtime-acceptance.json) now identifies two
nonproduction Workers in the Studio account. Their HTTPS release metadata
matches the shipping source SHA, artifact digest, and fixture candidate ID.
Both pass static-asset and unknown-route checks. Astro preserves its custom
404 and script-free HTML; Next.js additionally passes real cookie-dependent
rendering, private cache headers, cookie/query route handling, and application
module byte comparison.

Both initial attempts retained failed state. Astro's generic Python probe was
blocked with Cloudflare error 1010; a reproducing test and an explicit Studio
probe identifier resolve it. Next.js metadata passed but its first HTML
request returned 404; a later read returned the expected HTML. Reconciliation
passes without redeployment. The read-only cf deployment API confirms each
Worker still has exactly its original deployment/version.

Local follow-up validation passes all 136 Python tests with both native
Workers suites enabled, four Swift tests, actionlint, and runtime assembly.
The fixture commit's hosted push and PR runs pass, including the Linux workerd
job. Hosted validation of the subsequent probe change is tracked separately.
Test-environment production promotion, prior complete release/routing recovery,
and all actual product cutovers remain open. Keep the two fixture Workers for
those exercises and remove them after provider acceptance finishes.
