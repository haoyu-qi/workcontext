"""Manifest-owned skill synchronization: checksums, platform filter, conflicts, rollback."""
import hashlib,json,os,re,shutil,tempfile,uuid
from pathlib import Path

SECRET=re.compile(rb'(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})')
FORBIDDEN={'.env','auth.json','credentials.json','config.yaml','SOUL.md','MEMORY.md','USER.md'}
def sha(data):return hashlib.sha256(data).hexdigest()
def files(skill):
    result={}
    if not (skill/'SKILL.md').is_file():raise ValueError('missing_SKILL.md')
    for p in sorted(skill.rglob('*')):
        if p.is_symlink():raise ValueError('symlink_not_allowed')
        if p.is_file():
            if p.name in FORBIDDEN or p.suffix.lower() in ['.pem','.key','.p12']:raise ValueError('private_file_not_allowed')
            raw=p.read_bytes()
            if len(raw)>10*1024*1024 or SECRET.search(raw):raise ValueError('oversized_or_secret_file')
            result[p.relative_to(skill).as_posix()]=sha(raw)
    return result

def build(source,names,platforms=None):
    source=Path(source).resolve();items=[]
    for name in names:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,80}',name):raise ValueError('invalid_skill_name')
        folder=source/name
        if folder.is_symlink():raise ValueError('symlink_not_allowed')
        items.append({'name':name,'platforms':(platforms or {}).get(name,['macos','windows','linux']),'files':files(folder)})
    return {'schema':'workcontext.skills.v1','skills':items}

def plan(source,target,manifest,platform):
    source=Path(source).resolve();target=Path(target).resolve();statepath=target/'.workcontext-sync.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {'skills':{}}
    if manifest.get('schema')!='workcontext.skills.v1' or not isinstance(manifest.get('skills'),list):raise ValueError('invalid_manifest')
    rows=[];seen=set()
    for item in manifest['skills']:
        name=item['name']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,80}',name) or name in seen:raise ValueError('invalid_skill_name')
        seen.add(name)
        if platform not in item['platforms']:rows.append({'name':name,'action':'skip_platform'});continue
        folder=source/name
        if folder.is_symlink() or files(folder)!=item['files']:raise ValueError('source_checksum_mismatch')
        dest=target/name
        if dest.is_symlink():raise ValueError('target_symlink_not_allowed')
        old=state['skills'].get(name)
        if not dest.exists():action='install'
        else:
            current=files(dest)
            if current==item['files']:action='unchanged'
            elif old is not None and current==old:action='update'
            else:action='conflict'
        rows.append({'name':name,'action':action})
    return {'changes':rows,'conflicts':[r['name'] for r in rows if r['action']=='conflict']}

def apply(source,target,manifest,platform):
    source=Path(source).resolve();target=Path(target).resolve();result=plan(source,target,manifest,platform)
    if result['conflicts']:raise ValueError('local_skill_conflict: '+', '.join(result['conflicts']))
    target.mkdir(parents=True,exist_ok=True);statepath=target/'.workcontext-sync.json';before=statepath.read_bytes() if statepath.exists() else None
    state=json.loads(before) if before else {'skills':{}};txn=uuid.uuid4().hex
    backup=target.parent/'.workcontext-skill-backups'/txn;backup.mkdir(parents=True,mode=0o700)
    changed={r['name']:r['action'] for r in result['changes'] if r['action'] in ['install','update']}
    for name,action in changed.items():
        if action=='update':shutil.copytree(target/name,backup/name)
    applied=[]
    record={'transaction':txn,'target':str(target),'changed':changed,'before_state':json.loads(before) if before else None,'installed':{}}
    try:
        for item in manifest['skills']:
            name=item['name']
            if platform not in item['platforms']:continue
            if name in changed:
                staging=Path(tempfile.mkdtemp(prefix='.skill-',dir=target));shutil.copytree(source/name,staging/'content')
                if files(staging/'content')!=item['files']:raise ValueError('source_changed_during_sync')
                if (target/name).exists():
                    if changed[name]!='update' or files(target/name)!=state['skills'].get(name):raise ValueError('target_changed_during_sync')
                    shutil.rmtree(target/name)
                applied.append(name)
                (staging/'content').replace(target/name);staging.rmdir()
            state['skills'][name]=item['files'];record['installed'][name]=item['files']
        recordpath=backup/'transaction.json';recordpath.write_text(json.dumps(record,indent=2));recordpath.chmod(0o600)
        tmp=target/('.workcontext-sync-'+txn+'.tmp');tmp.write_text(json.dumps(state,indent=2));tmp.chmod(0o600);tmp.replace(statepath)
    except Exception:
        for name in reversed(applied):
            action=changed[name]
            if (target/name).exists():shutil.rmtree(target/name)
            if action=='update':shutil.copytree(backup/name,target/name)
        if before is not None:statepath.write_bytes(before)
        elif statepath.exists():statepath.unlink()
        raise
    return {**result,'transaction':txn,'rollback':str(backup/'transaction.json')}

def rollback(recordpath):
    recordpath=Path(recordpath);record=json.loads(recordpath.read_text());target=Path(record['target']);statepath=target/'.workcontext-sync.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {'skills':{}}
    for name in record['changed']:
        dest=target/name
        if dest.is_symlink() or not dest.is_dir() or files(dest)!=record['installed'][name]:raise ValueError('rollback_conflict')
        if state['skills'].get(name)!=record['installed'][name]:raise ValueError('rollback_state_conflict')
    for name,action in record['changed'].items():
        shutil.rmtree(target/name)
        if action=='update':shutil.copytree(recordpath.parent/name,target/name)
        previous=(record['before_state'] or {'skills':{}})['skills'].get(name)
        if previous is None:state['skills'].pop(name,None)
        else:state['skills'][name]=previous
    statepath.write_text(json.dumps(state,indent=2));statepath.chmod(0o600)
    return {'rolled_back':record['transaction']}
