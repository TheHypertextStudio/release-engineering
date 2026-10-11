"""Promote immutable artifacts through native provider tools."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request


def execute(command, *, cwd=None, capture=False):
    result = subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def _deployment(component, environment):
    deploy = dict(component.get("deploy", {}))
    environments = deploy.pop("environments", {})
    if environment in environments:
        entry = environments[environment]
        if isinstance(entry, dict):
            deploy.update(entry)
    elif environment != "production":
        raise ValueError(f"No {environment} deployment binding for {component['id']}")
    return deploy


def cloud_run_plan(component, artifact, environment):
    image = artifact.get("image", "")
    if not re.fullmatch(r"[A-Za-z0-9./_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Cloud Run promotion requires an immutable image digest")
    deploy = _deployment(component, environment)
    for name in ("project", "region", "service"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", deploy.get(name, "")):
            raise ValueError(f"Cloud Run requires a valid {name}")
    revision = artifact.get("revision", "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", revision) or not revision.startswith(deploy["service"] + "-"):
        raise ValueError("Revision must be an immutable service-prefixed identifier")
    flags = ["--project", deploy["project"], "--region", deploy["region"], "--quiet"]
    command = ["gcloud", "run", "deploy", deploy["service"], "--image", image,
               "--revision-suffix", revision[len(deploy["service"]) + 1:],
               "--no-traffic", "--tag", "studio-review", *flags]
    if deploy.get("runtime_service_account"):
        command.extend(["--service-account", deploy["runtime_service_account"]])
    if deploy.get("env_file"):
        command.extend(["--env-vars-file", deploy["env_file"]])
    if deploy.get("secret_bindings"):
        command.extend(["--set-secrets", deploy["secret_bindings"]])
    move = ["gcloud", "run", "services", "update-traffic", deploy["service"], "--to-revisions", f"{revision}=100", *flags]
    return [command, move]


def worker_plan(component, artifact, environment):
    deploy = component.get("deploy", {})
    bindings = deploy.get("environments", {})
    binding = bindings.get(environment)
    if not isinstance(binding, str) or not binding:
        raise ValueError("Worker requires an explicit environment binding")
    entrypoint = artifact.get("entrypoint", "")
    if not entrypoint or Path(entrypoint).is_absolute() or ".." in Path(entrypoint).parts:
        raise ValueError("Worker requires a prebuilt relative entrypoint")
    return ["pnpm", "exec", "wrangler", "deploy", entrypoint, "--no-bundle", "--config", deploy["config"], *(["--env", binding] if binding != "default" else [])]


def probe(url, *, expected_sha=None, expected_metadata=None, attempts=8, interval=3):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Release probes require an HTTPS URL without credentials")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={
                    "Accept": "application/json", "User-Agent": "Studio-Release-Probe/1.0"}), timeout=20) as response:
                if response.geturl().split(":", 1)[0] != "https":
                    raise ValueError("Release probes cannot downgrade HTTPS")
                payload = response.read(262145)
                if len(payload) > 262144:
                    raise ValueError("Health response exceeded its bound")
                if expected_sha is not None and json.loads(payload).get("source_sha") != expected_sha:
                    raise ValueError("Production health returned another source revision")
                if expected_metadata is not None:
                    actual = json.loads(payload)
                    if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in expected_metadata.items()):
                        raise ValueError("Production health returned another website candidate")
                return {"status": "passed", "url": url}
        except (OSError, ValueError):
            if attempt + 1 == attempts:
                raise RuntimeError("Release health probe failed") from None
            time.sleep(interval)


def deploy_cloud_run(component, artifact, environment, *, runner=execute):
    deploy = _deployment(component, environment)
    commands = cloud_run_plan(component, artifact, environment)
    runner(commands[0])
    raw = runner(["gcloud", "run", "services", "describe", deploy["service"], "--project", deploy["project"], "--region", deploy["region"], "--format=json"], capture=True)
    service = json.loads(raw)
    urls = [traffic["url"] for traffic in service.get("status", {}).get("traffic", []) if traffic.get("tag") == "studio-review" and traffic.get("revisionName") == artifact["revision"]]
    if len(urls) != 1:
        raise RuntimeError("Provider did not expose the reviewed revision")
    revision = json.loads(runner(["gcloud","run","revisions","describe",artifact["revision"],"--project",deploy["project"],"--region",deploy["region"],"--format=json"],capture=True))
    recorded = revision.get("status",{}).get("imageDigest")
    if recorded != artifact["image"]:
        raise RuntimeError("Provider revision contains another image digest")
    probe(urls[0].rstrip("/") + deploy.get("health_path", "/health"), expected_sha=artifact.get("source_sha"))
    if environment == "production" or deploy.get("move_traffic", True):
        runner(commands[1])
    if deploy.get("health_url"):
        probe(deploy["health_url"],expected_sha=artifact.get("source_sha"))
    return {"revision": artifact["revision"], "image": artifact["image"], "state": "completed"}


def hosting_upload_command(bucket, source, destination, *, immutable=True):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", bucket) or ".." in Path(destination).parts or destination.startswith("/"):
        raise ValueError("Invalid hosting destination")
    command = ["gcloud", "storage", "cp", str(source), f"gs://{bucket}/{destination}"]
    if immutable:
        command.append("--if-generation-match=0")
    return command


def upload_asset(bucket, source, destination, *, immutable=True, runner=execute):
    source = Path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = f"gs://{bucket}/{destination}"
    if immutable:
        try:
            previous = json.loads(runner(["gcloud", "storage", "objects", "describe", target, "--format=json"], capture=True))
        except subprocess.CalledProcessError:
            previous = None
        if previous is not None:
            if previous.get("metadata", {}).get("studio-sha256") != digest:
                raise ValueError("Existing immutable asset has another digest")
            return {"sha256": digest, "object": destination}
    command = hosting_upload_command(bucket, source, destination, immutable=immutable)
    command.append(f"--custom-metadata=studio-sha256={digest}")
    runner(command)
    return {"sha256": digest, "object": destination}


def verify_download(bucket,destination,expected):
    url='https://storage.googleapis.com/'+urllib.parse.quote(bucket,safe='')+'/'+urllib.parse.quote(destination,safe='/')
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache'})
    digest=hashlib.sha256()
    with urllib.request.urlopen(request,timeout=30) as response:
        for block in iter(lambda:response.read(128*1024),b''):
            digest.update(block)
    if digest.hexdigest()!=expected:
        raise ValueError('Public download differs from the reviewed artifact')
    return {'url':url,'sha256':expected,'status':'passed'}


def provision(config, *, env, apply=False, runner=execute):
    if not re.fullmatch(r"[a-z][a-z0-9-]*",env):
        raise ValueError("Invalid provisioning environment")
    infrastructure = config.data.get("infrastructure", {})
    path = infrastructure.get("path")
    if not path or not (config.root / path).resolve().is_relative_to(config.root):
        raise ValueError("Product requires a repository-local Terraform root")
    directory = (config.root / path).resolve()
    variables = infrastructure.get("variable_files", {}).get(env)
    if not variables:
        raise ValueError("Provisioning requires an explicit environment variable file")
    if not (directory / variables).resolve().is_relative_to(directory) or not (directory / variables).is_file():
        raise ValueError("Provisioning variables must remain inside the infrastructure root")
    def run_step(command):
        result = runner(command, cwd=directory)
        if isinstance(result, int) and result != 0:
            raise RuntimeError(f"Terraform {command[1]} failed with exit status {result}")

    run_step(["terraform", "init", "-input=false"])
    plan = directory / f"{env}.tfplan"
    if not apply:
        run_step(["terraform", "plan", "-input=false", f"-var-file={variables}", f"-out={plan}"])
    else:
        if not plan.is_file():
            raise ValueError("Provisioning apply requires its reviewed saved plan")
        run_step(["terraform", "apply", "-input=false", str(plan)])


def _native_cli(tool, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+',str(version)):
        raise ValueError(f'{tool} requires an exact native CLI version')
    return ['pnpm','dlx',f'{tool}@{version}']


def deploy_worker(component, artifact, environment, build_root, bindings_root, version, *, runner=execute):
    deploy=component['deploy']
    original=(bindings_root/component['path']/deploy['config']).resolve()
    if not original.is_relative_to(bindings_root.resolve()) or not original.is_file():
        raise ValueError('Worker configuration must come from the immutable candidate')
    binding=Path(build_root)/Path(deploy['config']).name
    if original!=binding.resolve():
        import shutil
        shutil.copyfile(original,binding)
    configured={**component,'deploy':{**deploy,'config':str(binding)}}
    command=worker_plan(configured,{'entrypoint':deploy['entrypoint']},environment)
    cli=_native_cli('wrangler',version)
    migrations=component.get('migrations')
    if migrations:
        if migrations.get('type')!='d1' or not migrations.get('compatible_contract'):
            raise ValueError('Migrations require an accepted installed-client compatibility contract')
        if not (Path(build_root)/migrations['directory']).is_dir():
            raise ValueError('Candidate does not contain reviewed migrations')
        named=deploy['environments'][environment]
        runner([*cli,'d1','migrations','apply',migrations['database'],'--remote','--config',str(binding),*(['--env',named] if named!='default' else [])],cwd=build_root)
    runner([*cli,*command[3:],'--var',f'STUDIO_SOURCE_SHA:{artifact["source_sha"]}','--var',f'STUDIO_ARTIFACT_SHA256:{artifact["sha256"]}'],cwd=build_root)
    health=deploy.get('health_urls',{}).get(environment)
    if not health: raise ValueError('Worker promotion requires an explicit production probe')
    probe(health,expected_sha=artifact['source_sha'] if deploy.get('health_provenance',True) else None)
    return {'state':'completed','artifact_sha256':artifact['sha256']}


def deploy_site(component, artifact, environment, build_root, toolchain, *, bindings_root=None, runner=execute, journal=None, authorize=None):
    deploy=component['deploy']; provider=deploy.get('provider')
    binding=deploy.get('environments',{}).get(environment)
    if binding is None: raise ValueError(f'Site lacks isolated {environment} bindings')
    health=deploy.get('health_urls',{}).get(environment)
    if not health: raise ValueError('Site requires a declared health probe')
    if provider=='workers':
        from .websites import workers_site_inputs, seed_workers_site_cache
        parsed=urllib.parse.urlparse(health)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Website requires an HTTPS health probe without credentials')
        cli=_native_cli('wrangler',toolchain.get('wrangler'))
        command,metadata=workers_site_inputs(component,artifact,environment,build_root,bindings_root or build_root)
        cache=seed_workers_site_cache(component,metadata,environment,build_root,
            command[command.index('--config')+1],cli,runner=runner,journal=journal,
            journal_root=bindings_root or build_root,authorize=authorize)
        if authorize is not None: authorize()
        runner([*cli,*command],cwd=build_root)
        probe(health,expected_metadata=metadata)
        return {'state':'completed','artifact_sha256':artifact['sha256'],'candidate_id':artifact['candidate_id'], **({'cache':cache} if cache else {})}
    elif provider=='pages':
        project=binding.get('project') if isinstance(binding,dict) else binding
        if not project: raise ValueError('Pages requires an explicit project')
        branch=deploy.get('production_branch','main') if environment=='production' else f'candidate-{artifact["source_sha"]}'
        runner([*_native_cli('wrangler',toolchain.get('wrangler')),'pages','deploy',str(build_root),'--project-name',project,'--branch',branch,'--commit-hash',artifact['source_sha']],cwd=build_root)
    elif provider=='vercel':
        if not isinstance(binding,dict) or not binding.get('project_id') or not binding.get('org_id'):
            raise ValueError('Vercel requires immutable project and organization bindings')
        if not __import__('os').environ.get('VERCEL_TOKEN'): raise ValueError('Vercel release credential is missing')
        directory=Path(build_root)/'.vercel'; directory.mkdir(exist_ok=True)
        (directory/'project.json').write_text(json.dumps({'projectId':binding['project_id'],'orgId':binding['org_id']}))
        # Vercel prebuilt output belongs under .vercel/output. Promotion only moves those bytes.
        result=runner([*_native_cli('vercel',toolchain.get('vercel')),'deploy','--prebuilt','--yes',*(['--prod'] if environment=='production' else []),'-m',f'studioSourceSha={artifact["source_sha"]}','-m',f'studioArtifactSha256={artifact["sha256"]}'],cwd=build_root,capture=True)
        if not result or not result.splitlines()[-1].startswith('https://'): raise RuntimeError('Vercel did not return an immutable deployment URL')
        probe(result.splitlines()[-1],expected_sha=None)
    else:
        raise ValueError('Site requires a shared distribution adapter')
    probe(health,expected_sha=artifact['source_sha'] if deploy.get('health_provenance',False) else None)
    return {'state':'completed','artifact_sha256':artifact['sha256']}


def publish_npm(component, archive, *, runner=execute):
    import base64
    import os
    import tarfile
    publish=component['publish']
    if publish.get('registry')!='https://npm.pkg.github.com' or not os.environ.get('NODE_AUTH_TOKEN'):
        raise ValueError('GitHub package publication requires its scoped registry credential')
    with tarfile.open(archive) as package:
        data=json.load(package.extractfile('package/package.json'))
    if data['name']!=publish['package']: raise ValueError('Reviewed package name differs from its registry binding')
    expected='sha512-'+base64.b64encode(hashlib.sha512(Path(archive).read_bytes()).digest()).decode()
    version=f'{data["name"]}@{data["version"]}'
    try:
        actual=runner(['pnpm','view',version,'dist.integrity','--registry',publish['registry']],capture=True)
    except subprocess.CalledProcessError:
        actual=None
    if actual:
        if actual!=expected: raise ValueError('Published package version contains different bytes')
    else:
        runner(['pnpm','publish',str(archive),'--registry',publish['registry'],'--no-git-checks'])
    actual=runner(['pnpm','view',version,'dist.integrity','--registry',publish['registry']],capture=True)
    if actual!=expected: raise ValueError('Registry did not publish the reviewed package digest')
    return {'state':'completed','package':version,'integrity':expected}
