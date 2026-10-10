"""GitHub-owned candidate provenance and promotion orchestration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from .candidate import CandidateError, PromotionJournal, load_candidate, sha256, validate_promotion, write_candidate
from .config import load_config
from .lifecycle import run
from .providers import deploy_cloud_run, deploy_worker, deploy_site, publish_npm, execute, probe, upload_asset, worker_plan, verify_download
from .versioning import build_number, version_from_git


def api(route, *, method=None, payload=None):
    command = ['gh', 'api', route]
    if method:
        command += ['--method', method]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def verify_run(manifest, record, default_branch):
    expected = {'id': manifest['workflow_run_id'], 'run_attempt': manifest['workflow_run_attempt'],
                'head_sha': manifest['source_sha'], 'head_branch': default_branch,
                'event': manifest.get('trigger_event','push'), 'conclusion': 'success', 'path': '.github/workflows/candidate.yml'}
    if expected['event'] not in {'push','repository_dispatch'} or any(record.get(key) != value for key, value in expected.items()) or record.get('repository', {}).get('full_name') != manifest['repository']:
        raise CandidateError('Candidate did not originate in a successful default-branch candidate run')


def verify_hosted(candidate):
    repository = candidate.data['repository']
    metadata = api(f'repos/{repository}')
    record = api(f'repos/{repository}/actions/runs/{candidate.data["workflow_run_id"]}/attempts/{candidate.data["workflow_run_attempt"]}')
    verify_run(candidate.data, record, metadata['default_branch'])


def promotion_order(components):
    rank = {'cloud-run':0, 'cloudflare-worker':0, 'swiftpm':1, 'pnpm':1, 'gradle':1, 'macos':2, 'static-site':3}
    return sorted(components, key=lambda component: rank[component['kind']])


def candidate_build_number(offset, run_number, attempt):
    if type(attempt) is not int or not 1 <= attempt < 1000:
        raise ValueError('Candidate attempt must be between 1 and 999')
    return build_number(offset, int(run_number) * 1000 + attempt)


def assert_current_authorization(candidate, records):
    current = [record for record in records if record.get('candidate_id') == candidate.data['id']]
    if len(current) != 1 or current[0].get('manifest_sha256') != candidate.digest or current[0].get('authorization') != 'authorized':
        raise CandidateError('Candidate authorization is absent, revoked, or replaced')
    if any(record.get('build_number',0) > candidate.data['build_number'] for record in records):
        raise CandidateError('A newer approved candidate supersedes this candidate')
    return True


def authorizations(repository):
    records = []
    for release in api(f'repos/{repository}/releases?per_page=100'):
        if release['tag_name'].startswith('promotion-'):
            try:
                records.append(json.loads(release['body']))
            except (ValueError,TypeError):
                raise CandidateError('Product promotion history contains an invalid authorization') from None
    return records


def production_baseline(repository):
    delivered=[]
    for release in api(f'repos/{repository}/releases?per_page=100'):
        if not release['tag_name'].startswith('production-'): continue
        record=json.loads(release['body'])
        operations=record.get('operations',{})
        if record.get('state')=='completed' or any(value.get('state')=='completed' for name,value in operations.items() if name.endswith((':direct',':store-release'))):
            delivered.append(record)
    return max(delivered,key=lambda record:record['build_number']) if delivered else None


def require_completed(result):
    if not isinstance(result,dict) or result.get('state') != 'completed':
        raise CandidateError('A prerequisite operation did not complete')
    return result


def artifact(path, output, component, channel, **metadata):
    path = Path(path)
    return {'component': component, 'channel': channel, 'path': path.relative_to(output).as_posix(),
            'sha256': sha256(path), 'size': path.stat().st_size, **metadata}


def pack_tree(source, target):
    source = Path(source).resolve()
    if not source.is_dir():
        raise CandidateError(f'Native build output is missing: {source.name}')
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_symlink() and not path.resolve().is_relative_to(source):
                raise CandidateError('Build output symlink leaves the artifact')
            if path.is_file():
                archive.write(path, path.relative_to(source))
    if not zipfile.ZipFile(target).namelist():
        raise CandidateError('Native build output is empty')


def unpack(source, target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            if not (target / info.filename).resolve().is_relative_to(target) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise CandidateError('Artifact contains a path escape or symlink')
        archive.extractall(target)


def _check_names(config):
    return [check['name'] if isinstance(check, dict) else f'{component["id"]}:{index}'
            for component in config.components for index, check in enumerate(component['checks'])]


def create(config, output, tooling_revision):
    if os.environ.get('GITHUB_EVENT_NAME') not in {'push','repository_dispatch'} or os.environ.get('GITHUB_REF') != f'refs/heads/{api("repos/" + config["repository"])["default_branch"]}':
        raise CandidateError('Candidates require a push to the default branch')
    if os.environ.get('GITHUB_REPOSITORY') != config['repository']:
        raise CandidateError('Candidate declaration belongs to another repository')
    lock = json.loads((config.root / 'studio.lock.json').read_text())
    if tooling_revision != lock['revision'] or not re.fullmatch('[0-9a-f]{40}', tooling_revision):
        raise CandidateError('Candidate tooling revision must match the product lock')
    source_sha = execute(['git','rev-parse','HEAD'], cwd=config.root, capture=True)
    if source_sha != os.environ.get('GITHUB_SHA'):
        raise CandidateError('Candidate checkout differs from the push revision')
    execute(['git','diff','--exit-code','HEAD','--'],cwd=config.root,capture=True)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    run(config, 'check')
    version = version_from_git(config.root, config['release'].get('next_version'),previous=production_baseline(config['repository']))
    number = candidate_build_number(config['release'].get('build_offset', 0), os.environ['GITHUB_RUN_NUMBER'], int(os.environ['GITHUB_RUN_ATTEMPT']))
    identifier = f'{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}'
    from . import bindings
    resolved,files=bindings.snapshot(config,output,source_sha,api)
    artifacts=[artifact(path,output,'_bindings','policy') for path in files]
    for component in resolved:
        kind = component['kind']
        directory = output / component['id']
        directory.mkdir()
        if kind == 'macos':
            from . import macos, store
            for channel in config['release']['channels']:
                adapter = macos if channel == 'direct' else store
                prepared=config
                if channel=='direct':
                    import copy
                    from .config import Config
                    policy=copy.deepcopy(config.data)
                    policy['release']['macos']['sparkle']['download_url']=policy['release']['macos']['sparkle']['download_url'].replace('{candidate_id}',identifier)
                    prepared=Config(config.root,policy)
                paths = adapter.prepare(prepared, component, directory / channel, version, number, channel)
                artifacts += [artifact(path, output, component['id'], channel) for path in paths]
        elif kind == 'cloud-run':
            build = component['build']
            tag = f'{build["image_repository"]}:candidate-{identifier}'
            execute(['docker','build','--label',f'org.opencontainers.image.revision={source_sha}',
                     '--file',build.get('dockerfile','Dockerfile'),'--tag',tag,*[argument for key,value in build.get('arguments',{}).items() for argument in ('--build-arg',f'{key}={value}')],'.'], cwd=config.root / component['path'])
            execute(['docker','push',tag])
            digest = execute(['docker','inspect','--format={{index .RepoDigests 0}}',tag], capture=True)
            descriptor = directory / 'image.json'
            descriptor.write_text(json.dumps({'image':digest, 'source_sha':source_sha}))
            artifacts.append(artifact(descriptor, output, component['id'], 'service', image=digest,
                                      revision=f'{component["deploy"]["environments"]["production"]["service"]}-{identifier}',source_sha=source_sha))
        else:
            run(config, 'build', component=component['id'])
            source = config.root / component['path'] / component['build']['output']
            for relative in component['build'].get('include',[]):
                origin = (config.root / component['path'] / relative).resolve()
                if Path(relative).is_absolute() or not origin.is_relative_to(config.root) or '..' in Path(relative).parts:
                    raise CandidateError('Build inputs must stay inside the product repository')
                destination = source / relative
                if origin.is_dir():
                    shutil.copytree(origin,destination,dirs_exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(origin,destination)
            target = directory / 'build.zip'
            pack_tree(source, target)
            artifacts.append(artifact(target, output, component['id'], 'web' if kind == 'static-site' else 'service',
                                      entrypoint=component.get('deploy', {}).get('entrypoint'), source_sha=source_sha))
        if component.get('deploy',{}).get('config'):
            relative = Path(component['path']) / component['deploy']['config']
            binding = output / relative
            if not binding.resolve().is_relative_to(output):
                raise CandidateError('Provider bindings must stay inside the candidate')
            binding.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(config.root / relative,binding)
            artifacts.append(artifact(binding,output,'_bindings','policy'))
    # Preserve the declared policy with the bytes being reviewed. Promotion cannot read a newer branch.
    snapshot = output / 'studio.yaml'
    shutil.copyfile(config.root / 'studio.yaml', snapshot)
    artifacts.append(artifact(snapshot, output, '_policy', 'policy'))
    prerequisites = {'tooling-pin':'passed'}
    compatibility = {}
    for dependency in config['release'].get('dependencies', []):
        reference = ready_dependency(dependency, output)
        artifacts += reference['artifacts']
        compatibility[dependency['id']] = {'status':'passed', 'manifest_sha256':reference['manifest_sha256']}
    data = {'schema':1, 'id':identifier, 'product':config.product, 'repository':config['repository'],
            'source_sha':source_sha, 'trigger_event':os.environ['GITHUB_EVENT_NAME'], 'workflow_run_id':int(os.environ['GITHUB_RUN_ID']),
            'workflow_run_attempt':int(os.environ['GITHUB_RUN_ATTEMPT']), 'version':version,
            'build_number':number, 'tooling_revision':tooling_revision, 'toolchains':config['toolchain'],
            'artifacts':artifacts, 'checks':{name:'passed' for name in _check_names(config)},
            'prerequisites':prerequisites, 'compatibility':compatibility}
    execute(['git','diff','--exit-code','HEAD','--'],cwd=config.root,capture=True)
    candidate = write_candidate(output / 'candidate.json', data)
    # Staging consumes the candidate's immutable files. It never receives production identities.
    for component in config.components:
        if component['kind'] in {'cloud-run','cloudflare-worker','static-site'}:
            promote_component(config, candidate, component, 'staging')
    write_review(candidate, output)
    return candidate


def download(repository, identifier, destination):
    if not re.fullmatch(r'[1-9][0-9]*-[1-9][0-9]*', identifier):
        raise CandidateError('Candidate id must be run-id and attempt')
    run_id, attempt = map(int, identifier.split('-'))
    # Artifact names include the attempt, so a rerun cannot replace approved bytes.
    try:
        execute(['gh','run','download',str(run_id),'--repo',repository,'--name',f'candidate-{identifier}','--dir',str(destination)])
    except subprocess.CalledProcessError:
        with tempfile.TemporaryDirectory() as temporary:
            execute(['gh','release','download',f'component-ready-{identifier}','--repo',repository,'--pattern',f'candidate-{identifier}.zip','--dir',temporary])
            unpack(Path(temporary)/f'candidate-{identifier}.zip',destination)
    candidate = load_candidate(Path(destination) / 'candidate.json', expected_repository=repository)
    if candidate.data['id'] != identifier or candidate.data['workflow_run_id']!=run_id or candidate.data['workflow_run_attempt']!=attempt:
        raise CandidateError('Downloaded artifact belongs to another candidate')
    verify_hosted(candidate)
    return candidate


def ready_dependency(dependency, output):
    repository = dependency['repository']
    # A supporting repo owns its ready record. Selection happens only while assembling a new candidate.
    releases = api(f'repos/{repository}/releases?per_page=100')
    ready = [release for release in releases if release['tag_name'].startswith('component-ready-') and release['draft']]
    if not ready:
        raise CandidateError(f'No accepted supporting candidate exists for {repository}')
    release = sorted(ready, key=lambda item:item['created_at'], reverse=True)[0]
    identifier = release['tag_name'].removeprefix('component-ready-')
    destination = output / 'dependencies' / dependency['id']
    candidate = download(repository, identifier, destination)
    declared = load_config(destination / 'studio.yaml')
    for records in ('checks','prerequisites','compatibility'):
        from .candidate import _statuses
        if any(status != 'passed' for status in _statuses(candidate.data[records]).values()):
            raise CandidateError('Supporting candidate has unresolved gates')
    if declared.product != dependency.get('product') or declared['owner_repository'] != dependency['owner_repository']:
        raise CandidateError('Supporting candidate targets another product')
    if dependency.get('compatibility_version') not in declared['release'].get('compatibility_versions', []):
        raise CandidateError('Supporting candidate does not support the installed client contract')
    paths = [candidate.path, candidate.path.with_suffix('.json.sha256')]
    paths += [destination / entry['path'] for entry in candidate.data['artifacts']]
    return {'manifest_sha256':candidate.digest, 'artifacts':[artifact(path,output,dependency['id'],'dependency') for path in paths]}


def write_review(candidate, output):
    data = candidate.data
    previous = api(f'repos/{data["repository"]}/releases?per_page=100')
    completed=[]
    for item in previous:
        if item['tag_name'].startswith('production-') and not item['draft']:
            try: delivered=json.loads(item['body'])
            except (ValueError,TypeError): continue
            if delivered.get('state')=='completed' or any(operation.get('state')=='completed' and (name.endswith(':direct') or name.endswith(':store-release')) for name,operation in delivered.get('operations',{}).items()):
                completed.append(item)
    prior = max(completed, key=lambda item:item['published_at'])['target_commitish'] if completed else None
    comparison = f'https://github.com/{data["repository"]}/compare/{prior}...{data["source_sha"]}' if prior else f'https://github.com/{data["repository"]}/commit/{data["source_sha"]}'
    (Path(output) / 'review.md').write_text(f'# Review candidate {data["id"]}\n\nThe reviewer must install the shipping artifact and attach the declared acceptance evidence before invoking Promote.\n\nManifest SHA-256: `{candidate.digest}`.\n\n[Changes since production]({comparison}).\n\nThe candidate contains version {data["version"]} and numeric build {data["build_number"]}. Each artifact digest appears in candidate.json. Staging does not replace installation and upgrade evidence for these bytes.\n')


def record_ready(config, candidate):
    tag = f'component-ready-{candidate.data["id"]}'
    existing=next((entry for entry in api(f'repos/{config["repository"]}/releases?per_page=100') if entry['tag_name']==tag),None)
    if existing:
        if existing['body']!=f'Manifest SHA-256: {candidate.digest}':
            raise CandidateError('A duplicate readiness notification names different bytes')
    else:
        execute(['gh','release','create',tag,'--repo',config['repository'],'--target',candidate.data['source_sha'],
                 '--draft','--title',f'Component candidate {candidate.data["id"]}','--notes',f'Manifest SHA-256: {candidate.digest}'])
    bundle_name=f'candidate-{candidate.data["id"]}.zip'
    if not existing or not any(asset['name']==bundle_name for asset in existing.get('assets',[])):
        with tempfile.TemporaryDirectory() as temporary:
            bundle=Path(temporary)/bundle_name
            pack_tree(candidate.path.parent,bundle)
            execute(['gh','release','upload',tag,str(bundle),'--repo',config['repository']])
    if config['repository'] != config['owner_repository']:
        if not os.environ.get('STUDIO_DISPATCH_TOKEN'):
            raise CandidateError('Cross-repository notification requires the product-scoped dispatch credential')
        env = {**os.environ,'GH_TOKEN':os.environ['STUDIO_DISPATCH_TOKEN']}
        subprocess.run(['gh','api',f'repos/{config["owner_repository"]}/dispatches','--input','-'],
                       input=json.dumps({'event_type':'component-ready','client_payload':{'repository':config['repository'], 'candidate_id':candidate.data['id'], 'manifest_sha256':candidate.digest}}),
                       env=env,text=True,check=True,capture_output=True)


def promote_component(config, candidate, component, environment):
    from .bindings import component as bound_component
    component=bound_component(candidate,component)
    matches = [item for item in candidate.data['artifacts'] if item['component'] == component['id']]
    if component['kind'] == 'cloud-run':
        if len(matches) != 1:
            raise CandidateError('Service must select one immutable image descriptor')
        bound = dict(matches[0])
        deploy = component['deploy']['environments'][environment]
        bound['revision'] = f'{deploy["service"]}-{candidate.data["id"]}'
        binding=dict(deploy)
        if binding.get('env_file'): binding['env_file']=str(candidate.path.parent/binding['env_file'])
        configured={**component,'deploy':{**component['deploy'],'environments':{**component['deploy']['environments'],environment:binding}}}
        return deploy_cloud_run(configured,bound,environment)
    if component['kind'] in {'cloudflare-worker','static-site','pnpm'}:
        if len(matches)!=1: raise CandidateError('Component must select one immutable archive')
        with tempfile.TemporaryDirectory() as staging:
            unpack(candidate.path.parent/matches[0]['path'],staging)
            root=Path(staging)
            if component['kind']=='cloudflare-worker':
                return deploy_worker(component,matches[0],environment,root,candidate.path.parent,config['toolchain'].get('wrangler'))
            if component['kind']=='pnpm':
                packages=list(root.glob('*.tgz'))
                if len(packages)!=1: raise CandidateError('Package candidate must contain exactly one native package tarball')
                return publish_npm(component,packages[0])
            if component.get('build',{}).get('profile')=='vercel-prebuilt':
                output=root/'.vercel'/'output'; output.mkdir(parents=True)
                for path in list(root.iterdir()):
                    if path.name!='.vercel': shutil.move(str(path),output/path.name)
            website_artifact={**matches[0],'candidate_id':candidate.data['id']}
            return deploy_site(component,website_artifact,environment,root,config['toolchain'],bindings_root=candidate.path.parent)
    if component['kind'] == 'macos':
        if environment != 'production':
            raise CandidateError('Shipping identities do not deploy to staging')
        from . import store
        result = {}
        hosting = config['release'].get('hosting',{})
        for entry in matches:
            if entry['channel'] == 'direct':
                source = candidate.path.parent / entry['path']
                if source.name == 'appcast.xml':
                    continue
                destination = f'{config.product}/releases/{candidate.data["id"]}/{source.name}'
                result[source.name] = upload_asset(hosting['bucket'],source,destination)
                result[source.name]['probe']=verify_download(hosting['bucket'],destination,entry['sha256'])
        # Publish the feed only after all immutable objects exist and services pass production checks.
        for entry in matches:
            if entry['channel'] == 'direct' and Path(entry['path']).name == 'appcast.xml':
                result['feed'] = upload_asset(hosting['bucket'],candidate.path.parent / entry['path'],f'{config.product}/appcast.xml',immutable=False)
                result['feed']['probe']=verify_download(hosting['bucket'],f'{config.product}/appcast.xml',entry['sha256'])
        return {'state':'completed', **result}
    raise CandidateError(f'{component["kind"]} requires a distribution adapter before promotion')


def _restore_asset(repository, release, name, directory):
    if any(asset['name']==name for asset in release.get('assets',[])):
        execute(['gh','release','download',release['tag_name'],'--repo',repository,'--pattern',name,'--dir',str(directory),'--clobber'])
        return True
    return False


def _open_journal(config,candidate,review,journal_path):
    repository=config['repository']
    tag=f'promotion-{candidate.data["id"]}'
    releases=api(f'repos/{repository}/releases?per_page=100')
    release=next((item for item in releases if item['tag_name']==tag),None)
    directory=Path(journal_path).parent
    directory.mkdir(parents=True,exist_ok=True)
    authorization={'candidate_id':candidate.data['id'],'manifest_sha256':candidate.digest,
                   'build_number':candidate.data['build_number'],'source_sha':candidate.data['source_sha'],
                   'reviewer':review['reviewer'],'authorization':'authorized'}
    if release is None:
        execute(['gh','release','create',tag,'--repo',repository,'--target',candidate.data['source_sha'],
                 '--draft','--title',f'Promotion {candidate.data["id"]}','--notes',json.dumps(authorization,sort_keys=True)])
    else:
        if json.loads(release['body']) != authorization:
            raise CandidateError('Existing promotion authorization is revoked or differs from review')
        _restore_asset(repository,release,'journal.json',directory)
        _restore_asset(repository,release,'authorization.json',directory)
    review_path=directory / 'authorization.json'
    if review_path.exists() and json.loads(review_path.read_text()) != review:
        raise CandidateError('A retry cannot replace its recorded review evidence')
    review_path.write_text(json.dumps(review,sort_keys=True))
    execute(['gh','release','upload',tag,str(review_path),'--repo',repository,'--clobber'])
    save=lambda path:execute(['gh','release','upload',tag,str(path),'--repo',repository,'--clobber'])
    return PromotionJournal(journal_path,candidate.digest,on_save=save)


def _authorization_check(config,candidate):
    return assert_current_authorization(candidate,authorizations(config['repository']))


def _promotion_state(journal):
    states=[record['state'] for record in journal.data['operations'].values()]
    if states and all(state=='completed' for state in states):
        return 'completed'
    if any(state in {'failed','partial','running'} for state in states):
        return 'partial' if 'completed' in states else 'failed'
    return 'apple-pending' if 'apple-pending' in states else 'pending'


def _publish_record(config,candidate,journal,*,publish_hosting=True,state_override=None):
    state=state_override or _promotion_state(journal)
    metadata={'schema':1,'product':config.product,'candidate_id':candidate.data['id'],
              'source_sha':candidate.data['source_sha'],'manifest_sha256':candidate.digest,
              'version':candidate.data['version'],'build_number':candidate.data['build_number'],
              'state':state,'operations':journal.data['operations']}
    path=journal.path.parent / 'release.json'
    path.write_text(json.dumps(metadata,sort_keys=True,indent=2)+'\n')
    repository=config['repository']; tag=f'production-{candidate.data["id"]}'
    releases=api(f'repos/{repository}/releases?per_page=100')
    existing=next((release for release in releases if release['tag_name']==tag),None)
    if existing is None:
        execute(['gh','release','create',tag,'--repo',repository,'--target',candidate.data['source_sha'],
                 '--title',f'{config.product} {candidate.data["version"]} ({candidate.data["build_number"]})',
                 '--notes',json.dumps(metadata,sort_keys=True)])
    else:
        api(f'repos/{repository}/releases/{existing["id"]}',method='PATCH',payload={'body':json.dumps(metadata,sort_keys=True)})
    execute(['gh','release','upload',tag,str(path),str(candidate.path),str(candidate.path.with_suffix('.json.sha256')),'--repo',repository,'--clobber'])
    hosting=config['release'].get('hosting',{})
    if publish_hosting and hosting and any(record.get('state')=='completed' for name,record in journal.data['operations'].items() if name.endswith(':direct')):
        _authorization_check(config,candidate)
        upload_asset(hosting['bucket'],path,f'{config.product}/release.json',immutable=False)
    return metadata


def promote(config,candidate,review,journal_path):
    verify_hosted(candidate)
    validate_promotion(candidate,config,review)
    if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.environ.get('GITHUB_ACTOR')!=review['reviewer']:
        raise CandidateError('The workflow actor must be the reviewer named in authorization')
    journal=_open_journal(config,candidate,review,journal_path)
    _authorization_check(config,candidate)
    try:
        _authorization_check(config,candidate)
        # Dependency operations must complete before clients become available.
        for dependency in config['release'].get('dependencies',[]):
            directory=candidate.path.parent / 'dependencies' / dependency['id']
            dependent=load_candidate(directory / 'candidate.json')
            verify_hosted(dependent)
            declared=load_config(directory / 'studio.yaml')
            for component in promotion_order(declared.components):
                _authorization_check(config,candidate)
                result=journal.perform(f'dependency:{dependency["id"]}:{component["id"]}',lambda c=component,d=declared,m=dependent:promote_component(d,m,c,'production'))
                require_completed(result)
        for component in promotion_order(config.components):
            _authorization_check(config,candidate)
            if component['kind']=='macos':
                if 'direct' in config['release']['channels']:
                    journal.perform(component['id']+':direct',lambda c=component:promote_component(config,candidate,c,'production'))
                if 'app-store' in config['release']['channels']:
                    from . import store
                    journal.perform(component['id']+':store-upload',lambda c=component:store.upload(config,candidate,c,authorized_digest=candidate.digest))
                    journal.perform(component['id']+':store-review',lambda c=component:store.submit(config,candidate,c,authorized_digest=candidate.digest))
            else:
                require_completed(journal.perform(component['id'],lambda c=component:promote_component(config,candidate,c,'production')))

    finally:
        try:
            _authorization_check(config,candidate)
        except CandidateError:
            metadata=_publish_record(config,candidate,journal,publish_hosting=False)
        else:
            metadata=_publish_record(config,candidate,journal)
    return metadata


def reconcile(config):
    results=[]
    for release in api(f'repos/{config["repository"]}/releases?per_page=100'):
        if not release['tag_name'].startswith('promotion-'): continue
        authorization=json.loads(release['body'])
        if authorization.get('authorization')!='authorized': continue
        with tempfile.TemporaryDirectory(prefix='studio-reconcile-dispatch-') as temporary:
            directory=Path(temporary)
            if not _restore_asset(config['repository'],release,'journal.json',directory): continue
            journal=json.loads((directory/'journal.json').read_text())
            if not any(record['state']=='apple-pending' for record in journal['operations'].values()): continue
            candidate=download(config['repository'],authorization['candidate_id'],directory/'candidate')
            if candidate.digest!=authorization['manifest_sha256']: raise CandidateError('Store candidate differs from approval')
            execute(['gh','workflow','run','promote.yml','--repo',config['repository'],
                     '-f','release_action=reconcile-one','-f',f'candidate_id={candidate.data["id"]}',
                     '-f',f'manifest_sha256={candidate.digest}','-f','evidence={}'])
            results.append({'candidate_id':candidate.data['id'],'state':'dispatched'})
    return results


def reconcile_one(config,candidate,journal_path):
    from . import store
    verify_hosted(candidate)
    release=next((value for value in api(f'repos/{config["repository"]}/releases?per_page=100') if value['tag_name']==f'promotion-{candidate.data["id"]}'),None)
    if release is None: raise CandidateError('Store reconciliation requires existing human approval')
    directory=Path(journal_path).parent; directory.mkdir(parents=True,exist_ok=True)
    for name in ('journal.json','authorization.json'):
        if not _restore_asset(config['repository'],release,name,directory): raise CandidateError('Store approval evidence is missing')
    review=json.loads((directory/'authorization.json').read_text())
    validate_promotion(candidate,config,review)
    _authorization_check(config,candidate)
    save=lambda path:execute(['gh','release','upload',release['tag_name'],str(path),'--repo',config['repository'],'--clobber'])
    journal=PromotionJournal(journal_path,candidate.digest,on_save=save)
    def authorized(value): return _authorization_check(config,value)
    try:
        for component in config.components:
            if component['kind']!='macos' or 'app-store' not in config['release']['channels']: continue
            authorized(candidate)
            reviewed=journal.perform(component['id']+':store-review',lambda:store.submit(config,candidate,component,authorized_digest=candidate.digest))
            if reviewed.get('state')=='failed': continue
            outcome=journal.perform(component['id']+':store-release',lambda:store.reconcile(config,candidate,component,authorized_digest=candidate.digest,authorize=authorized))
            if outcome.get('state')=='completed':
                journal.data['operations'][component['id']+':store-review']['state']='completed'
                journal._save()
    finally:
        try:
            _authorization_check(config,candidate)
        except CandidateError:
            metadata=_publish_record(config,candidate,journal,publish_hosting=False)
        else:
            metadata=_publish_record(config,candidate,journal)
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=['create','promote','ready','download','reconcile','reconcile-one','withdraw'])
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,default=Path('.studio/candidate'))
    parser.add_argument('--tooling-revision')
    parser.add_argument('--candidate-id')
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    config = load_config(args.root / 'studio.yaml')
    if args.mode == 'reconcile':
        print(json.dumps(reconcile(config)))
    elif args.mode == 'download':
        candidate = download(config['repository'],args.candidate_id,args.output)
        if candidate.digest != args.manifest_sha256:
            raise CandidateError('Downloaded manifest does not match review')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as target:
                target.write(f'source_sha={candidate.data["source_sha"]}\ntooling_revision={candidate.data["tooling_revision"]}\n')
        print(candidate.data['source_sha'])
    elif args.mode == 'create':
        create(config,args.output,args.tooling_revision)
    else:
        candidate = load_candidate(args.output / 'candidate.json',expected_repository=config['repository'],expected_digest=args.manifest_sha256)
        if args.mode == 'ready':
            record_ready(config,candidate)
        else:
            review = json.loads(os.environ['STUDIO_REVIEW_EVIDENCE'])
            if args.mode=='withdraw':
                from .recovery import withdraw
                result=withdraw(config,candidate,args.output.parent/'journal.json')
            else:
                result = reconcile_one(config,candidate,args.output.parent/'journal.json') if args.mode=='reconcile-one' else promote(config,candidate,review,args.output.parent / 'journal.json')
            print(json.dumps(result))
            if result['state'] in {'failed','partial'}: raise SystemExit(1)

if __name__ == '__main__':
    main()
