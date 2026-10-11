"""The product lifecycle command line."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from .candidate import CandidateError, PromotionJournal, load_candidate, validate_promotion
from .config import ConfigError, load_config
from .lifecycle import run


def parser():
    result = argparse.ArgumentParser(prog='studio')
    result.add_argument('--root', type=Path, default=Path.cwd())
    result.add_argument('--config', type=Path, default=Path('studio.yaml'))
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('setup', 'doctor', 'dev', 'check', 'build'):
        command = commands.add_parser(name)
        command.add_argument('--component')
    docs = commands.add_parser('docs')
    docs.add_argument('mode', choices=('inspect',))
    docs.add_argument('--project', required=True)
    docs.add_argument('--source-sha', required=True)
    docs.add_argument('--deployment-id', required=True)
    site = commands.add_parser('site')
    site_commands = site.add_subparsers(dest='site_command', required=True)
    recovery = site_commands.add_parser('recovery')
    recovery.add_argument('mode', choices=('capture', 'restore'))
    recovery.add_argument('--component', required=True)
    recovery.add_argument('--env', choices=('staging', 'production'), required=True)
    recovery.add_argument('--record', type=Path, required=True)
    recovery.add_argument('--journal', type=Path)
    recovery.add_argument('--archive', type=Path)
    recovery.add_argument('--worker-config', type=Path)
    recovery.add_argument('--expected-current-version')
    recovery.add_argument('--expected-current-routing-sha256')
    recovery.add_argument('--confirm')
    provision = commands.add_parser('provision')
    provision.add_argument('mode', choices=('plan', 'apply'))
    provision.add_argument('--env', required=True)
    release = commands.add_parser('release')
    release.add_argument('mode', choices=('inspect', 'promote'))
    release.add_argument('--candidate', required=True)
    release.add_argument('--evidence', type=Path)
    release.add_argument('--manifest-sha256')
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        root = arguments.root.resolve()
        config = load_config(arguments.config if arguments.config.is_absolute() else root / arguments.config)
        if arguments.command == 'docs':
            from . import documentation
            observation = documentation.inspect(config, arguments.project, arguments.source_sha, arguments.deployment_id)
            path = documentation.record(config, observation)
            print(json.dumps({**observation, 'record': str(path)}, indent=2))
            return {'completed': 0, 'failed': 1, 'provider-pending': 2}[observation['state']]
        if arguments.command == 'site':
            from .providers import execute
            from .websites import (CloudflareWorkersAPI, capture_workers_site_recovery,
                                   restore_workers_site_recovery)
            component = next((row for row in config.components if row.get('id') == arguments.component), None)
            if component is None or component.get('kind') != 'static-site' or component.get('deploy', {}).get('provider') != 'workers':
                raise ValueError('Site recovery requires a declared Workers static-site component')
            record_path = arguments.record if arguments.record.is_absolute() else root / arguments.record
            api = CloudflareWorkersAPI()
            if arguments.mode == 'capture':
                if not arguments.archive:
                    raise ValueError('Recovery capture requires the retained prior --archive')
                worker_config = arguments.worker_config or (Path(component['path']) / component['deploy']['config'])
                worker_config = worker_config if worker_config.is_absolute() else root / worker_config
                archive = arguments.archive if arguments.archive.is_absolute() else root / arguments.archive
                result = capture_workers_site_recovery(component, arguments.env, worker_config,
                                                       archive, record_path, api=api)
            else:
                if not arguments.expected_current_version or not arguments.expected_current_routing_sha256 or not arguments.confirm:
                    raise ValueError('Recovery restore requires expected live version, route/domain digest, and explicit --confirm')
                worker_config = Path(component['path']) / component['deploy']['config']
                worker_config = worker_config if worker_config.is_absolute() else root / worker_config
                result = restore_workers_site_recovery(
                    record_path, component, arguments.env,
                    current_config_path=worker_config,
                    expected_current_version=arguments.expected_current_version,
                    expected_current_routing_sha256=arguments.expected_current_routing_sha256,
                    confirmation=arguments.confirm, toolchain=config['toolchain'], api=api,
                    runner=lambda command, **kwargs: execute(command, cwd=root, **kwargs),
                    journal_path=(arguments.journal if arguments.journal is None or arguments.journal.is_absolute()
                                  else root / arguments.journal))
            print(json.dumps(result, indent=2))
            return 0
        if arguments.command == 'release':
            if __import__('re').fullmatch(r'[1-9][0-9]*-[1-9][0-9]*',arguments.candidate):
                from .ci import download
                directory=root/'.studio'/'inspect'/arguments.candidate
                if not (directory/'candidate.json').exists(): download(config['repository'],arguments.candidate,directory)
                candidate_path=directory/'candidate.json'
            else:
                path=Path(arguments.candidate)
                candidate_path=path if path.is_absolute() else root/path
            candidate = load_candidate(candidate_path, expected_product=config.product, expected_repository=config['repository'], expected_digest=arguments.manifest_sha256)
            if arguments.mode == 'inspect':
                print(json.dumps({'manifest_sha256': candidate.digest, **candidate.data}, indent=2))
                return 0
            if config['repository'] != config['owner_repository']:
                raise CandidateError('Promotion must run from the owner repository')
            if not arguments.evidence:
                raise CandidateError('Promotion requires --evidence bound to the candidate digest')
            evidence_path = arguments.evidence if arguments.evidence.is_absolute() else root / arguments.evidence
            review = json.loads(evidence_path.read_text())
            validate_promotion(candidate, config, review)
            product_root = candidate.data.get('product_root', '.')
            command = ['gh', 'workflow', 'run', 'promote.yml', '--repo', config['owner_repository'], '-f', f'candidate_id={candidate.data["id"]}', '-f', f'manifest_sha256={candidate.digest}', '-f', f'evidence={evidence_path.read_text()}', '-f', f'product_root={product_root}']
            def dispatch():
                subprocess.run(command, cwd=root, check=True)
                return {'state': 'completed', 'dispatched': True}
            dispatch()
            print(json.dumps({'candidate_id': candidate.data['id'], 'manifest_sha256': candidate.digest, 'status': 'dispatched'}))
            return 0
        result = run(config, arguments.command, component=getattr(arguments, 'component', None), env=getattr(arguments, 'env', None), apply=getattr(arguments, 'mode', None) == 'apply')
        if arguments.command == 'doctor':
            print(json.dumps(result, indent=2))
            return 0 if all(result.values()) else 1
        return 0
    except (CandidateError, ConfigError, RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'studio: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
