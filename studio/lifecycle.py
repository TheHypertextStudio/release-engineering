"""Dispatch bounded native lifecycle commands in component directories."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import ConfigError, argv


def execute(command, cwd):
    return subprocess.run(command, cwd=cwd, check=False).returncode


def native_command(component, action):
    kind = component['kind']
    build = component.get('build', {})
    if action == 'dev':
        if component.get('dev'):
            return argv(component['dev'], 'dev')
        if kind in {'pnpm', 'static-site', 'cloudflare-worker', 'cloud-run'}:
            return ['pnpm', 'run', 'dev']
        raise ConfigError(f'{component.get("id", kind)} has no development command')
    if action == 'setup':
        if kind in {'swiftpm', 'macos'}:
            return ['swift', 'package', 'resolve'] if kind == 'swiftpm' else ['xcodebuild', '-resolvePackageDependencies', *_xcode_target(component)]
        if kind in {'pnpm', 'static-site', 'cloudflare-worker', 'cloud-run'}:
            return ['pnpm', 'install', '--frozen-lockfile']
        if kind == 'gradle':
            return ['./gradlew', 'dependencies', '--max-workers=2', '--no-daemon']
    if action == 'build':
        profile=build.get('profile')
        if profile=='cloudflare-worker':
            deploy=component['deploy']; environment=build.get('environment','staging')
            binding=deploy.get('environments',{}).get(environment)
            if not isinstance(binding,str) or not binding:
                raise ConfigError('Worker build requires an explicit environment')
            return ['pnpm','exec','wrangler','deploy','--dry-run','--config',deploy['config'],'--outdir',build['output'],*(['--env',binding] if binding!='default' else [])]
        if profile=='npm-package':
            return ['pnpm','pack','--pack-destination',build['output']]
        if profile=='vercel-prebuilt':
            return ['pnpm','dlx','vercel@'+str(build.get('vercel_version','50.28.1')),'build','--yes','--prod']
        if kind == 'swiftpm':
            configuration = build.get('configuration', 'release')
            if configuration not in {'debug', 'release'}:
                raise ConfigError('Swift build configuration must be debug or release')
            return ['swift', 'build', '-c', configuration, '--jobs', '2']
        if kind == 'macos':
            return ['xcodebuild', *_xcode_target(component), '-configuration', build.get('configuration', 'Release'), '-jobs', '2', 'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO', 'build']
        if kind == 'gradle':
            tasks = build.get('tasks', ['assemble'])
            if not isinstance(tasks, list) or not tasks or any(not isinstance(task, str) or not task or task.startswith('-') for task in tasks):
                raise ConfigError('Gradle build requires task names')
            return ['./gradlew', *tasks, '--max-workers=2', '--no-daemon']
        if kind in {'pnpm', 'static-site', 'cloudflare-worker', 'cloud-run'}:
            script = build.get('script', 'build')
            if not isinstance(script, str) or not script or script.startswith('-'):
                raise ConfigError('pnpm build requires a script name')
            return ['pnpm', 'run', script]
    raise ConfigError(f'Unsupported {action} for {kind}')


def _xcode_target(component):
    target = []
    if component.get('workspace'):
        target += ['-workspace', component['workspace']]
    elif component.get('project'):
        target += ['-project', component['project']]
    else:
        raise ConfigError('macOS component requires project or workspace')
    if not component.get('scheme'):
        raise ConfigError('macOS component requires scheme')
    return [*target, '-scheme', component['scheme']]


def doctor(config, runner=execute):
    tools = {'git', 'gh', 'python3'}
    required = set()
    for component in config.components:
        kind = component['kind']
        if kind in {'pnpm', 'cloud-run', 'static-site', 'cloudflare-worker'}:
            tools.add('pnpm')
        if kind in {'macos', 'swiftpm'}:
            tools.add('swift')
        if kind == 'macos':
            tools.update({'xcodebuild', 'xcrun'})
    report = {f'tool:{tool}': shutil.which(tool) is not None for tool in sorted(tools)}
    report['python-runtime'] = sys.version_info >= (3, 11)
    if any(item['kind'] == 'macos' for item in config.components):
        required.update({'APPLE_CERTIFICATE_BASE64', 'APPLE_CERTIFICATE_PASSWORD', 'APPLE_API_PRIVATE_KEY', 'APPLE_API_KEY_ID', 'APPLE_API_ISSUER'})
        if 'direct' in config.data['release'].get('channels', []):
            required.add('SPARKLE_PRIVATE_KEY')
    report.update({f'credential:{name}': bool(os.environ.get(name)) for name in sorted(required)})
    for name, declared in config.data['toolchain'].items():
        if name not in {'python', 'xcode', 'swift', 'node', 'pnpm', 'java'}:
            continue
        command = {'python': ['python3', '--version'], 'xcode': ['xcodebuild', '-version'], 'swift': ['swift', '--version'], 'node': ['node', '--version'], 'pnpm': ['pnpm', '--version'], 'java': ['java', '-version']}[name]
        try:
            result = subprocess.run(command, cwd=config.root, capture_output=True, text=True, check=False)
            output = result.stdout + result.stderr
            expected = str(declared)
            if expected.startswith('>='):
                import re
                match = re.search(r'\d+(?:\.\d+)+', output)
                actual = tuple(map(int, match.group(0).split('.'))) if match else ()
                minimum = tuple(map(int, expected[2:].split('.')))
                report[f'version:{name}'] = result.returncode == 0 and actual >= minimum
            else:
                import re
                report[f'version:{name}'] = result.returncode == 0 and re.search(r'(?<![\d.])' + re.escape(expected) + r'(?![\d.])', output) is not None
        except OSError:
            report[f'version:{name}'] = False
    return report


def run(config, action, *, component=None, env=None, apply=False, runner=execute):
    if action == 'doctor':
        return doctor(config, runner)
    if action == 'provision':
        if not env:
            raise ValueError('Provision requires --env')
        from . import providers
        return providers.provision(config, env=env, apply=apply, runner=runner)
    if action == 'check' and (config.root / 'studio.lock.json').exists():
        from .conformance import check
        check(config)
    selected = [item for item in config.components if component is None or item['id'] == component]
    if not selected:
        raise ConfigError(f'Unknown component: {component}')
    results = []
    if action == 'setup':
        if any(item['kind']=='macos' for item in selected) and 'direct' in config['release'].get('channels',[]):
            from .tools import prepare_sparkle
            prepare_sparkle()
        bootstrap = config.root / 'bootstrap'
        if bootstrap.is_file():
            command = [str(bootstrap), 'worktree', 'prepare']
            if runner(command, config.root):
                raise RuntimeError('Worktree preparation failed')
            results.append(command)
    for item in selected:
        cwd = (config.root / item['path']).resolve()
        if not cwd.is_relative_to(config.root) or not cwd.is_dir():
            raise ConfigError(f'Component directory does not exist: {item["id"]}')
        if action=='build' and item.get('build',{}).get('profile')=='static-copy':
            source=(cwd/item['build']['source']).resolve(); target=(cwd/item['build']['output']).resolve()
            if not source.is_relative_to(config.root) or not target.is_relative_to(config.root) or source==target or target.is_relative_to(source):
                raise ConfigError('Static copy paths must be distinct repository directories')
            shutil.copytree(source,target,dirs_exist_ok=True)
            continue
        commands = [check.get('argv') if isinstance(check, dict) else check for check in item['checks']] if action == 'check' else [native_command(item, action)]
        for command in commands:
            if runner(command, cwd):
                raise RuntimeError(f'{action} failed for component {item["id"]}')
            results.append(command)
    return results
