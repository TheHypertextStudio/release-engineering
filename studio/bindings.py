"""Freeze nonsecret provider configuration while constructing a candidate."""
import copy
import json
from pathlib import Path
import re
import yaml

EXPRESSION=re.compile(r"\$\{\{\s*(github\.sha|vars\.([A-Z][A-Z0-9_]*))(?:\s*\|\|\s*'([^']*)')?\s*\}\}")

def render(value, variables, source_sha):
    if isinstance(value,dict):
        return {key:render(item,variables,source_sha) for key,item in value.items()}
    if isinstance(value,list):
        return [render(item,variables,source_sha) for item in value]
    if not isinstance(value,str):
        return value
    def replace(match):
        if match[1]=='github.sha': return source_sha
        if match[2] in variables and variables[match[2]]!='': return str(variables[match[2]])
        if match[3] is not None: return match[3]
        raise ValueError(f'Missing candidate configuration variable: {match[2]}')
    resolved=EXPRESSION.sub(replace,value)
    if '${{' in resolved: raise ValueError('Unsupported or secret expression in provider configuration')
    return resolved


def _uses_variables(value):
    if isinstance(value,dict):
        return any(_uses_variables(item) for item in value.values())
    if isinstance(value,list):
        return any(_uses_variables(item) for item in value)
    return isinstance(value,str) and any(match[2] for match in EXPRESSION.finditer(value))


def _environment_templates(config, components):
    templates={}
    for component in components:
        for environment,binding in component.get('deploy',{}).get('environments',{}).items():
            if not isinstance(binding,dict) or not binding.get('env_file_template'): continue
            origin=(config.root/component['path']/binding['env_file_template']).resolve()
            if not origin.is_relative_to(config.root): raise ValueError('Environment template escaped repository')
            templates[(component['id'],environment)]=yaml.safe_load(origin.read_text())
    return templates


def snapshot(config, output, source_sha, api):
    declared=copy.deepcopy(list(config.components))
    variables={}
    templates=None
    needs_variables=_uses_variables(declared)
    if not needs_variables:
        components=render(declared,variables,source_sha)
        templates=_environment_templates(config,components)
        needs_variables=any(_uses_variables(value) for value in templates.values())
    # Package-only candidates and literal provider configuration do not need
    # repository-variable API access. Still require it for real vars inputs.
    if needs_variables:
        for route in (f'repos/{config["repository"]}/actions/variables?per_page=100', f'repos/{config["repository"]}/environments/production/variables?per_page=100'):
            try: response=api(route)
            except Exception:
                if 'environments/' not in route: raise
                continue
            variables.update({item['name']:item['value'] for item in response.get('variables',[])})
        components=render(declared,variables,source_sha)
    if templates is None:
        templates=_environment_templates(config,components)
    files=[]
    for component in components:
        for environment, binding in component.get('deploy',{}).get('environments',{}).items():
            if not isinstance(binding,dict): continue
            template=binding.pop('env_file_template',None)
            if template:
                # Render parsed YAML so a variable cannot inject new environment entries.
                values=render(templates[(component['id'],environment)],variables,source_sha)
                values.update(STUDIO_SOURCE_SHA=source_sha)
                relative=Path('bindings')/component['id']/f'{environment}.env.yaml'
                target=output/relative; target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(yaml.safe_dump(values,sort_keys=True))
                binding['env_file']=str(relative)
                files.append(target)
    target=output/'bindings.json'
    target.write_text(json.dumps({'schema':1,'components':components},sort_keys=True,indent=2)+'\n')
    return components,[*files,target]


def component(candidate, declared):
    target=candidate.path.parent/'bindings.json'
    if not target.exists(): return declared
    values=json.loads(target.read_text())['components']
    matches=[value for value in values if value['id']==declared['id']]
    if len(matches)!=1: raise ValueError('Candidate provider bindings are incomplete')
    return matches[0]
