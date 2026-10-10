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

Curfew's current remote source uses `landing/` and `docs/`. Its divergent primary checkout remains untouched. That remote source says Mintlify is unpublished and the public setup guide remains `landing/docs.html`. An anonymous `/docs` response does not establish which source/project published it. Reconcile the live integration before changing any docs routing.

## Provider access and recovery

- Authenticated Cloudflare CLI access exposes LVBT only. The Studio account from source is unavailable. No Studio Worker preview, production operation, or usage baseline is verified.
- Vercel access is available in `williecubed-projects`. LogDate's canonical origin is `https://logdate.app`; Docket's is `https://clearthedocket.com`. The inventory records project IDs and currently observed production deployment IDs as recovery targets.
- Mintlify project settings, repository/source directory, deployment branch, contributor access, preview entitlement, and deployment result remain unverified.
- Anonymous home/docs requests succeeded for all six surfaces. Authenticated journeys, provider errors/latency/usage, complete route/authentication contracts, and Cloudflare recovery identities remain outstanding.

## Local implementation evidence

- The Workers site adapter accepts prebuilt modules/assets, frozen configuration, explicit account/environment bindings, and full release metadata. Website-only assembly stages and promotes the same archive after the mutable build directory is removed.
- Fresh review found unbounded Wrangler file inputs and shared production resource identities. Regression tests failed before the fixes. Supported file inputs now remain within the archive, and preview resource identities must differ from production. The pinned Wrangler dry run preserves reviewed module/chunk bytes and excludes outside files. All 117 Python checks, four Swift tests, workflow lint, and runtime assembly pass locally. Hosted CI and actual framework/provider acceptance remain open.
- The native package profile now uses `pnpm pack --pack-destination`; pnpm 11.9.0 rejects the previous `--outdir` argument.
- Shared Mintlify tooling compiles and installs as a packed package in a clean Node 24.20.0/pnpm 11.9.0 consumer checkout. Product configuration fixtures preserve every Curfew and Docket field. Native validators run against actual content.
- Docket's 52-page docs pass native build validation, links/anchors/redirects/snippets, and accessibility. Curfew's initial checks found two incorrect prefix links and insufficient primary-color contrast. The links are corrected; Willie approved `#b25e30` for docs text/controls while preserving marketing branding.

See [websites.md](websites.md) for the adapter and publishing contracts. Local checks do not establish shared package registry availability, live preview/production acceptance, completed rollout observation, or legacy hosting retirement.
