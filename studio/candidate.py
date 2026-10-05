"""Immutable release manifests, review gates, and durable operation state."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

class CandidateError(ValueError):
    pass

@dataclass(frozen=True)
class Candidate:
    data: dict
    path: Path
    digest: str


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(data):
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise CandidateError('Candidate requires schema 1')
    for key in ('id', 'product', 'repository', 'version'):
        if not isinstance(data.get(key), str) or not data[key]:
            raise CandidateError(f'Candidate requires {key}')
    if not re.fullmatch(r'[\w.-]+', data['id']):
        raise CandidateError('Candidate id must be an immutable identifier')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', data['repository']):
        raise CandidateError('Invalid candidate repository')
    if not re.fullmatch(r'\d+\.\d+\.\d+', data['version']):
        raise CandidateError('Invalid candidate version')
    for key in ('source_sha', 'tooling_revision'):
        if not isinstance(data.get(key), str) or not re.fullmatch(r'[0-9a-f]{40}', data[key]):
            raise CandidateError(f'{key} must be a full immutable commit SHA')
    for key in ('workflow_run_id', 'workflow_run_attempt', 'build_number'):
        if type(data.get(key)) is not int or data[key] < 1:
            raise CandidateError(f'{key} must be a positive integer')
    for key in ('checks', 'prerequisites', 'compatibility'):
        if not isinstance(data.get(key), (dict, list)):
            raise CandidateError(f'Candidate requires {key}')
    if not isinstance(data.get('artifacts'), list) or not data['artifacts']:
        raise CandidateError('Candidate requires artifacts')
    seen = set()
    for artifact in data['artifacts']:
        if not isinstance(artifact, dict):
            raise CandidateError('Invalid artifact')
        for key in ('component', 'channel', 'path', 'sha256'):
            if not isinstance(artifact.get(key), str) or not artifact[key]:
                raise CandidateError(f'Artifact requires {key}')
        path = Path(artifact['path'])
        if path.is_absolute() or '..' in path.parts or artifact['path'] in seen:
            raise CandidateError('Artifact paths must be distinct relative paths')
        seen.add(artifact['path'])
        if not re.fullmatch(r'[0-9a-f]{64}', artifact['sha256']) or type(artifact.get('size')) is not int or artifact['size'] < 0:
            raise CandidateError('Artifact requires SHA-256 and nonnegative size')


def write_candidate(path, data):
    _validate(data)
    path = Path(path)
    content = (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()
    digest = hashlib.sha256(content).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.with_suffix(path.suffix + '.sha256').exists():
        raise CandidateError('Candidate manifests cannot be replaced')
    with path.open('xb') as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    with path.with_suffix(path.suffix + '.sha256').open('x') as target:
        target.write(digest + '\n')
        target.flush()
        os.fsync(target.fileno())
    return load_candidate(path)


def load_candidate(path, *, expected_product=None, expected_repository=None, expected_tooling_revision=None, verify_artifacts=True, expected_digest=None):
    path = Path(path).resolve()
    try:
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        declared = path.with_suffix(path.suffix + '.sha256').read_text().strip().split()[0]
        if declared != digest or expected_digest is not None and expected_digest != digest:
            raise CandidateError('Candidate manifest checksum mismatch')
        data = json.loads(content)
        _validate(data)
        for key, expected in (('product', expected_product), ('repository', expected_repository), ('tooling_revision', expected_tooling_revision)):
            if expected is not None and data[key] != expected:
                raise CandidateError(f'Candidate {key} does not match declaration')
        if verify_artifacts:
            for artifact in data['artifacts']:
                local = (path.parent / artifact['path']).resolve()
                if not local.is_relative_to(path.parent) or not local.is_file() or local.stat().st_size != artifact['size'] or sha256(local) != artifact['sha256']:
                    raise CandidateError(f'Artifact digest mismatch: {artifact["path"]}')
        return Candidate(data, path, digest)
    except (OSError, json.JSONDecodeError, IndexError) as error:
        raise CandidateError(f'Cannot verify candidate: {error}') from error


def _statuses(value):
    if isinstance(value, dict):
        return {name: status.get('status') if isinstance(status, dict) else status for name, status in value.items()}
    if isinstance(value, list):
        if any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in value):
            raise CandidateError('Invalid named check records')
        records = {item['name']: item.get('status') for item in value}
        if len(records) != len(value):
            raise CandidateError('Duplicate check names')
        return records
    raise CandidateError('Invalid check records')


def validate_promotion(candidate, config, review):
    data = config.data if hasattr(config, 'data') else config
    verified = load_candidate(candidate.path, expected_digest=candidate.digest)
    if verified.data != candidate.data:
        raise CandidateError('Candidate in-memory data differs from immutable manifest')
    if data['repository'] != data.get('owner_repository', data['repository']):
        raise CandidateError('Only the owner repository can promote this product')
    if candidate.data['product'] != data['product'] or candidate.data['repository'] != data['repository']:
        raise CandidateError('Candidate does not belong to this product repository')
    policy = data['release']
    checks = _statuses(candidate.data['checks'])
    for name in policy.get('required_checks', []):
        if checks.get(name) != 'passed':
            raise CandidateError(f'Required check did not pass: {name}')
    for group in ('checks', 'prerequisites', 'compatibility'):
        if any(status != 'passed' for status in _statuses(candidate.data[group]).values()):
            raise CandidateError(f'Candidate has failed or unmet {group}')
    if not isinstance(review, dict) or review.get('schema') != 1 or review.get('manifest_sha256') != candidate.digest or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise CandidateError('Review must name a reviewer and bind the immutable manifest SHA-256')
    evidence = _statuses(review.get('checks', []))
    for name in policy.get('required_evidence', []):
        if evidence.get(name) != 'passed':
            raise CandidateError(f'Required release evidence is missing: {name}')
    for check in review.get('checks', []):
        if not isinstance(check.get('url'), str) or not check['url'].startswith(('https://', 'http://')):
            raise CandidateError('Release evidence requires a reviewable URL')


class PromotionJournal:
    def __init__(self, path, candidate_digest, on_save=None):
        self.on_save = on_save
        self.path = Path(path)
        if not re.fullmatch(r'[0-9a-f]{64}', candidate_digest):
            raise CandidateError('Journal requires candidate manifest digest')
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                raise CandidateError('Cannot read promotion journal') from error
            if self.data.get('manifest_sha256') != candidate_digest or self.data.get('schema') != 1:
                raise CandidateError('Promotion journal belongs to another candidate')
        else:
            self.data = {'schema': 1, 'manifest_sha256': candidate_digest, 'operations': {}}
            self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.journal-')
        try:
            with os.fdopen(descriptor, 'w') as target:
                json.dump(self.data, target, sort_keys=True, indent=2)
                target.write('\n')
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            if self.on_save is not None:
                self.on_save(self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def perform(self, name, operation):
        record = self.data['operations'].get(name, {})
        if record.get('state') == 'completed':
            return record.get('result')
        self.data['operations'][name] = {'state': 'running'}
        self._save()
        try:
            result = operation()
            state = result.get('state', 'completed') if isinstance(result, dict) else 'completed'
            if state not in {'completed', 'pending', 'apple-pending', 'partial', 'failed'}:
                raise CandidateError('Invalid promotion operation state')
            self.data['operations'][name] = {'state': state, 'result': result}
            self._save()
            return result
        except Exception:
            self.data['operations'][name] = {'state': 'failed'}
            self._save()
            raise
