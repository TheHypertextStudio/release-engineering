"""Withdraw an approved client without replacing or deleting its artifacts."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from .candidate import CandidateError, PromotionJournal
from .providers import execute

SPARKLE='http://www.andymatuschak.org/xml-namespaces/sparkle'


def remove_build(content,number):
    root=ET.fromstring(content)
    channel=root.find('channel')
    if channel is None: raise CandidateError('Update feed has no channel')
    for item in list(channel.findall('item')):
        enclosure=item.find('enclosure')
        version=item.find(f'{{{SPARKLE}}}version')
        build=enclosure.get(f'{{{SPARKLE}}}version') if enclosure is not None else None
        if str(number) in {build,version.text if version is not None else None}:
            channel.remove(item)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def replace_current(bucket,name,transform,*,runner=execute):
    target=f'gs://{bucket}/{name}'
    record=json.loads(runner(['gcloud','storage','objects','describe',target,'--format=json'],capture=True))
    generation=str(record['generation'])
    content=runner(['gcloud','storage','cat',target+'#'+generation],capture=True)
    replacement=transform(content)
    if replacement is None: return
    with tempfile.TemporaryDirectory(prefix='studio-withdraw-') as directory:
        path=Path(directory)/Path(name).name; path.write_bytes(replacement)
        runner(['gcloud','storage','cp',str(path),target,f'--if-generation-match={generation}',
                '--custom-metadata=studio-sha256='+hashlib.sha256(replacement).hexdigest()])


def withdraw(config,candidate,journal_path):
    from .ci import api,execute,verify_hosted,_restore_asset,_publish_record
    verify_hosted(candidate)
    if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or not os.environ.get('GITHUB_ACTOR'):
        raise CandidateError('Withdrawal requires an identified manual workflow actor')
    repository=config['repository']
    release=next((record for record in api(f'repos/{repository}/releases?per_page=100') if record['tag_name']==f'promotion-{candidate.data["id"]}'),None)
    if release is None: raise CandidateError('Withdrawal requires an existing exact approval')
    authorization=json.loads(release['body'])
    if authorization.get('manifest_sha256')!=candidate.digest:
        raise CandidateError('Withdrawal differs from the approved artifacts')
    if authorization.get('authorization') not in {'authorized','revoked'}:
        raise CandidateError('Invalid withdrawal authorization')
    authorization.update(authorization='revoked',withdrawn_by=os.environ['GITHUB_ACTOR'])
    # Revoke before touching distribution so interrupted recovery also blocks Apple release.
    api(f'repos/{repository}/releases/{release["id"]}',method='PATCH',payload={'body':json.dumps(authorization,sort_keys=True)})
    directory=Path(journal_path).parent; directory.mkdir(parents=True,exist_ok=True)
    if not _restore_asset(repository,release,'journal.json',directory):
        raise CandidateError('Withdrawal requires the recorded promotion journal')
    save=lambda path:execute(['gh','release','upload',release['tag_name'],str(path),'--repo',repository,'--clobber'])
    journal=PromotionJournal(journal_path,candidate.digest,on_save=save)
    def remove():
        hosting=config['release']['hosting']
        replace_current(hosting['bucket'],f'{config.product}/appcast.xml',lambda content:remove_build(content,candidate.data['build_number']))
        def metadata(content):
            record=json.loads(content)
            if record.get('candidate_id')!=candidate.data['id']: return None
            if record.get('manifest_sha256')!=candidate.digest: raise CandidateError('Current distribution metadata differs from approval')
            record['state']='withdrawn'
            return (json.dumps(record,sort_keys=True,indent=2)+'\n').encode()
        replace_current(hosting['bucket'],f'{config.product}/release.json',metadata)
        return {'state':'completed','withdrawn_by':os.environ['GITHUB_ACTOR']}
    try:
        journal.perform('withdraw:direct',remove)
    finally:
        result=_publish_record(config,candidate,journal,publish_hosting=False,state_override='withdrawn' if journal.data['operations'].get('withdraw:direct',{}).get('state')=='completed' else 'partial')
    return result
