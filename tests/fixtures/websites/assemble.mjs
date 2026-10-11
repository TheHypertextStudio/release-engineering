import { cp, readFile, rm, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
const [profile] = process.argv.slice(2);
if (!['astro', 'next'].includes(profile)) throw new Error('Unknown fixture profile');
// Wrangler completes OpenNext's final module bundle during candidate creation.
// Its deployable output contains no standalone Node dependency symlinks.
await rm('.artifact', { recursive: true, force: true });
const config = JSON.parse(await readFile('wrangler.json', 'utf8'));
config.no_bundle = false;
config.find_additional_modules = false;
delete config.rules;
const buildConfig = '.wrangler-fixture-build.json';
try {
  await writeFile(buildConfig, JSON.stringify(config));
  const result = spawnSync('pnpm', ['exec', 'wrangler', 'deploy', '--dry-run',
    '--config', buildConfig, '--env', 'staging', '--env-file', '/dev/null',
    '--autoconfig=false', '--outdir', '.artifact'], { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('Native Wrangler fixture bundling failed');
} finally {
  await rm(buildConfig, { force: true });
}
await cp(profile === 'astro' ? 'dist' : '.open-next/assets',
  profile === 'astro' ? '.artifact/assets' : '.artifact/.open-next/assets', { recursive: true });

