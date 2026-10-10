# Website release contract

Studio websites use the existing lifecycle and immutable candidate format. Products declare their native builds; release-engineering owns deployment and promotion. Mintlify remains the documentation renderer and publisher.

## Workers frontend artifacts

Use a `static-site` component with `deploy.provider: workers` for both static Astro sites and adapted Next.js applications. The artifact contains the prebuilt Worker modules and static assets. Next.js must finish its OpenNext adaptation during candidate construction.

The deployment declaration identifies `config`, `entrypoint`, `assets`, explicit `staging` and `production` environment names, and HTTPS `health_urls` for both environments. Paths are relative to the component/archive. Example declaration:

```yaml
components:
  - id: website
    kind: static-site
    path: .
    checks:
      - name: website
        argv: [pnpm, check]
    build:
      script: build:candidate
      output: build/site-candidate
    deploy:
      provider: workers
      config: wrangler.site.json
      entrypoint: worker.js
      assets: assets
      environments:
        staging: staging
        production: production
      health_urls:
        staging: https://staging.example.com/__studio/release
        production: https://example.com/__studio/release
```

The reviewed Wrangler site configuration is strict JSON, even if its filename ends in `.jsonc`. It declares the approved Cloudflare `account_id`, separate staging and production Worker names, and the asset binding. Custom `build` commands are forbidden at both root and environment level. Environment overrides cannot change the reviewed account. Staging route hosts must be explicit and must not overlap production hosts; a staging hostname wildcard is rejected.

Configure `run_worker_first` for `/__studio/release` so static asset routing cannot intercept the metadata endpoint. Preserve unknown-page 404s and existing product routes. Each environment must declare the bindings that its prebuilt Worker actually requires; framework compatibility and provider-resource acceptance are additional product gates.

## Candidate identity and promotion

The existing assembler supports website-only declarations without a native Mac component. It runs checks/builds, archives the output, freezes configuration and nonsecret bindings, writes the candidate manifest, and stages the unpacked candidate. Production promotion unpacks the same archive. It does not invoke a framework build.

The Workers site adapter passes the explicit entrypoint and asset directory to the pinned Wrangler CLI with bundling and autoconfiguration disabled. It disables implicit local environment-file loading. Candidate-relative paths and symlinks cannot escape the reviewed input. The existing Pages, Vercel, and standalone Worker adapters remain available.

Module roots and legacy file bindings are validated inside the extracted archive and rebased before Wrangler reads the copied configuration. Additional-module rules stay within that root. The site profile accepts an explicit set of reviewed Wrangler fields; unsupported filesystem/build options, including custom TypeScript configuration, Workers Sites, containers, unsafe bindings, and source-map uploads, fail before deployment. Add support through a reviewed artifact contract rather than bypassing the guard.

Declare resource bindings explicitly in both environments, using an empty array or mapping when that environment intentionally has none. Preview D1, R2, KV, queues (including dead-letter queues), services, external Durable Objects, Vectorize, Hyperdrive, workflows, datasets, pipelines, artifacts, dispatch namespaces, secret-store references, rate limiters, and tail consumers must have distinct provider identities from production. Worker-local Durable Objects remain isolated by the distinct Worker names. The adapter provides no shared-resource exception: a future exception needs demonstrated restricted access and a reviewed policy. These identity guards do not establish the permissions of provider credentials or the isolation of arbitrary backend URLs; verify those separately before preview acceptance.

Run `STUDIO_NATIVE_WORKERS_TESTS=1 python3 -m unittest discover -s tests -p 'test_workers_site*.py' -v` for the pinned Wrangler packaging check. It uses `--dry-run` and asserts that candidate-relative chunks retain their bytes and outside files are excluded. It never deploys to a provider account. Framework/workerd and authenticated provider acceptance remain separate gates.

After hashing, deployment injects:

- `STUDIO_SOURCE_SHA`: the reviewed source commit.
- `STUDIO_ARTIFACT_SHA256`: the complete website archive digest.
- `STUDIO_CANDIDATE_ID`: the immutable workflow run and attempt.

The Worker returns these as `sourceSha`, `artifactSha256`, and `candidateId` from `GET /__studio/release`, with `Cache-Control: no-store`. The health probe checks all three fields. A source-only response or wrong artifact/candidate identity fails acceptance. The artifact does not contain its own digest.

Approval, supersession, manifest/configuration verification, and resumable promotion remain in the existing candidate lifecycle. Provider CLI bootstrap and framework compatibility must also be verified before a product cutover; unit transport tests alone do not establish provider acceptance.

## Mintlify integration

Mintlify configuration and setup are standardized by the versioned `@thehypertextstudio/web-docs` package. Product-owned `docs.site.json` generates committed `docs.json`. Product MDX, branding, navigation, redirects, source directory, project, Git integration, and public docs URLs remain product-owned.

Keep required docs checks in product CI before merging to the Mintlify deployment branch. Use a pinned local CLI, and retain additional product checks. Record reviewed source SHA, required check results, Mintlify project/deployment identity, provider URL, and public path/search/asset/redirect acceptance independently from Workers candidates.

Mintlify has its own publishing lifecycle. It does not run a Workers build and does not provide this adapter's immutable artifact promotion or release metadata contract. Automated provider-result collection and verified Git settings are still required before documentation adoption is accepted.

Shared package publication uses the existing npm adapter. The package candidate contains one reviewed tarball; the adapter verifies the registry's SHA-512 integrity before accepting publication or an idempotent retry. Production promotion requires package-write permission in the calling workflow as well as the reusable workflow. Candidate and validation workflows retain package-read permission.

## Cutover and recovery

Before a cutover, record the previous complete deployment, Worker versions, routes/domain bindings, backend compatibility, and recovery instructions. Keep the previous Pages/Vercel deployment for the migration's retention period. Recovery restores matching HTML, modules, static assets, and route bindings together; data migrations remain forward compatible.

Provider cutover, a real failed-probe/retry/rollback exercise, and authenticated production acceptance remain explicit operations. The current adapter implementation does not by itself capture or restore live routes and Worker versions.
