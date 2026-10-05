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
            command = ['gh', 'workflow', 'run', 'promote.yml', '--repo', config['owner_repository'], '-f', f'candidate_id={candidate.data["id"]}', '-f', f'manifest_sha256={candidate.digest}', '-f', f'evidence={evidence_path.read_text()}']
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
