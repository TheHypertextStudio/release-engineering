# Framework shipping fixtures

This private workspace uses real Astro and Next.js/OpenNext builds. It runs
with Node 24.20.0 and pnpm 11.9.0. From the repository root:

```sh
pnpm --dir tests/fixtures/websites install --frozen-lockfile
STUDIO_FRAMEWORK_WORKERS_TESTS=1 python3 -m unittest discover -s tests -p test_workers_framework_runtime.py -v
```

The test creates each artifact once, archives it using the shared candidate
packager, and extracts independent staging and production roots. The native
Wrangler local server runs those shipping files in workerd with bundling
disabled. No Cloudflare account, token, or provider resource is used. The
32-character account ID in fixture config is intentionally fake.

Checks cover the three release identity fields, uncached metadata, real HTML,
Next.js application module bytes, static assets, unknown-route 404s, Astro's
custom 404, and script-free Astro HTML. The Next.js fixture additionally checks
server-rendered cookie isolation across Alice, Bob, anonymous, then Alice again,
and route-handler cookie/query handling. Fixture cookies are synthetic inputs,
not application sessions.

The framework build and final Wrangler dry-run bundle belong to candidate
creation. OpenNext's intermediate standalone directory contains dependency
symlinks and is not the shipping artifact. The final bundle and static assets
are copied into `.artifact`; staging and production only use that output.
Both environments explicitly declare ASSETS because asset bindings do not
inherit. Generated binding types come from pinned Wrangler.

Pins: Astro 7.2.9, Next.js 16.3.8, React/ReactDOM 19.2.7, OpenNext 1.20.10,
Wrangler 4.148.0. The adapter declares Next `>=15.5.27 <16 || >=16.3.8`.
This new fixture does not change existing product pins. The rclone.js
postinstall downloads a mutable external binary; it is explicitly disabled
because these fixtures do not use R2 cache population.

The Next.js fixture uses OpenNext's default dummy cache adapters. It proves
packaged execution and request isolation, not ISR, cache invalidation, image
optimization, streaming acceptance, product authentication, or backend parity.
Product compatibility checks remain required. The separate
[provider record](../../../docs/website-runtime-acceptance.json) records isolated
Cloudflare previews and test Workers using the production overlay, including
same-archive byte checks and failed-probe reconciliation. These operator
fixtures do not grant product promotion authorization. Hosted product
promotion and complete release/routing recovery remain separate gates.
