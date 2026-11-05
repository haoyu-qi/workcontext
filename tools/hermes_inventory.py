"""Portable read-only Hermes inventory. No .env, token values or databases are exported."""
import argparse,hashlib,json,re,sys
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

def inventory(home,include_soul=False):
    base=Path(home);config=base/'config.yaml';data={};parser='not_found'
    if config.exists():
        for pattern in ['installs/*/pm-runtime/generations/*/lib/python*/site-packages','installs/*/pm-runtime/generations/*/Lib/site-packages']:
            for p in base.glob(pattern):
                if (p/'ruamel/yaml').exists():sys.path.insert(0,str(p));break
        try:
            from ruamel.yaml import YAML
            data=YAML(typ='safe').load(config.read_text(encoding='utf-8')) or {};parser='ruamel'
        except ImportError:
            # Conservative scalar fallback, never print the complete config.
            parser='scalar_allowlist';section=None
            for line in config.read_text(encoding='utf-8').splitlines():
                match=re.match(r'^([A-Za-z_]+):\s*$',line)
                if match:section=match[1];data.setdefault(section,{});continue
                match=re.match(r'^\s+([A-Za-z_]+):\s*([^#]+?)\s*$',line)
                if match and section in ['model','memory','terminal','approvals'] and match[1] in ['default','provider','base_url','backend','mode']:
                    data[section][match[1]]=match[2].strip('"\'')
    model=data.get('model',{});model=model if isinstance(model,dict) else {'default':model}
    safe_model={k:model[k] for k in ['default','provider','base_url'] if k in model}
    if isinstance(safe_model.get('base_url'),str):
        u=urlsplit(safe_model['base_url']);safe_model['base_url']=urlunsplit((u.scheme,u.hostname or '',u.path,'',''))
    soul=base/'SOUL.md';text=soul.read_text(encoding='utf-8') if soul.exists() else ''
    def redact(t):
        return re.sub(r'(?i)(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|(?:api[_-]?key|password|token)\s*[:=]\s*["\']?[^\s"\']{12,})','[REDACTED]',t)
    result={'hermes_home':str(base),'config_exists':config.exists(),'parser':parser,'model':safe_model,
      'memory':{k:v for k,v in (data.get('memory') or {}).items() if k in ['provider','memory_enabled','user_profile_enabled']},
      'terminal_backend':(data.get('terminal') or {}).get('backend'),'approval_mode':(data.get('approvals') or {}).get('mode'),
      'mcp_names':sorted((data.get('mcp_servers') or {}).keys()),
      'soul':{'exists':soul.exists(),'sha256':hashlib.sha256(text.encode()).hexdigest() if text else None,'headings':[x for x in text.splitlines() if x.startswith('#')], 'characters':len(text)},
      'skill_count':len(list((base/'skills').rglob('SKILL.md'))) if (base/'skills').exists() else 0}
    if include_soul:result['soul']['text']=redact(text)
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--home',default=str(Path.home()/'.hermes'));p.add_argument('--include-soul',action='store_true');p.add_argument('--output');a=p.parse_args();r=inventory(a.home,a.include_soul);raw=json.dumps(r,ensure_ascii=False,indent=2)
    if a.output:
        f=Path(a.output);f.write_text(raw,encoding='utf-8')
        if sys.platform!='win32':f.chmod(0o600)
        print('Inventory saved: '+str(f))
    else:print(raw)
