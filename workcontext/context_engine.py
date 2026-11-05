"""Deterministic, evidence-backed, permission-first context materialization."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime
from contextlib import contextmanager
from .service import canonical,now,load,active,Rejected


def normalize_git(value):
    if not isinstance(value,str) or len(value)>512 or any(c.isspace() for c in value):raise ValueError('invalid_git_identity')
    scp=re.fullmatch(r'git@([A-Za-z0-9.-]+):([A-Za-z0-9._/-]+)',value)
    if scp:host,path=scp.groups()
    else:
        u=urlsplit(value)
        if u.scheme not in ['https','ssh'] or not u.hostname or u.query or u.fragment or u.password:raise ValueError('invalid_git_identity')
        if u.username and not (u.scheme=='ssh' and u.username=='git'):raise ValueError('credential_in_git_identity')
        host=u.hostname+((':'+str(u.port)) if u.port else '');path=u.path.lstrip('/')
    path=path.rstrip('/')
    if path.endswith('.git'):path=path[:-4]
    if not path or any(x in ('','.','..') for x in path.split('/')):raise ValueError('invalid_git_identity')
    return host.lower()+'/'+path


class Resolver:
    def __init__(self,catalog):
        self.projects={};self.repositories={};self.mappings={}
        for p in catalog:
            pid=p['project_id']
            if pid in self.projects:raise ValueError('duplicate_project_id')
            self.projects[pid]=p
            for raw in p.get('git_remotes',[]):
                alias=normalize_git(raw)
                if alias in self.repositories and self.repositories[alias]!=pid:raise ValueError('ambiguous_git_alias')
                self.repositories[alias]=pid
            for alias in p.get('mapping_keys',[]):
                if alias in self.mappings and self.mappings[alias]!=pid:raise ValueError('ambiguous_mapping_alias')
                self.mappings[alias]=pid
    def resolve(self,ref):
        if not ref:return None,'unresolved_no_reference'
        hits=[]
        if 'project_id' in ref:
            if ref['project_id'] not in self.projects:return None,'unresolved_unknown_id'
            hits.append(ref['project_id'])
        if 'git_remote' in ref:
            try:hit=self.repositories.get(normalize_git(ref['git_remote']))
            except ValueError:return None,'unresolved_invalid_git'
            if not hit:return None,'unresolved_unknown_git'
            hits.append(hit)
        if 'mapping_key' in ref:
            hit=self.mappings.get(ref['mapping_key'])
            if not hit:return None,'unresolved_unknown_mapping'
            hits.append(hit)
        if not hits:return None,'unresolved_no_identity'
        if len(set(hits))!=1:return None,'unresolved_conflict'
        return hits[0],'resolved'


def migrate(path,direction='up',backup=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(str(path));path.chmod(0o600)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():raise ValueError('refusing_p1_ledger')
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if direction=='up' and version==1:return
        if direction=='up' and version!=0:raise ValueError('unsupported_schema')
        if direction=='down':
            if version!=1:raise ValueError('unsupported_schema')
            if not backup or Path(backup).exists() or Path(backup).resolve()==path.resolve():raise ValueError('fresh_backup_required')
            with sqlite3.connect(str(backup)) as dest:db.backup(dest)
            Path(backup).chmod(0o600)
        sql=Path(__file__).resolve().parent/'migrations/p2'/('001-'+direction+'.sql')
        db.executescript(sql.read_text())
    finally:db.close()


@contextmanager
def context_db(path):
    db=sqlite3.connect(str(path),timeout=10);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        if db.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('migration_required')
        with db:yield db
    finally:db.close()


class Engine:
    def __init__(self,config):self.config=config
    def sync(self):
        c=load(self.config);resolver=Resolver(c['catalog']);changed=hashlib.sha256(canonical(c['catalog']).encode()).hexdigest()
        source=sqlite3.connect('file:'+str(Path(c['source_database']).resolve())+'?mode=ro',uri=True)
        try:
            with context_db(c['database']) as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute("SELECT value FROM sync_state WHERE key='catalog_hash'").fetchone()
                if not old or old[0]!=changed:
                    for table in ['facts','work_events','sessions','threads','tasks','repository_aliases','mapping_aliases','projects','sync_state']:db.execute('DELETE FROM '+table)
                    for pid,p in resolver.projects.items():db.execute('INSERT INTO projects VALUES (?,?)',(pid,p['name']))
                    for alias,pid in resolver.repositories.items():db.execute('INSERT INTO repository_aliases VALUES (?,?)',(alias,pid))
                    for alias,pid in resolver.mappings.items():db.execute('INSERT INTO mapping_aliases VALUES (?,?)',(alias,pid))
                    db.execute("INSERT INTO sync_state VALUES ('catalog_hash',?)",(changed,))
                row=db.execute("SELECT value FROM sync_state WHERE key='last_seq'").fetchone();seq=int(row[0]) if row else 0
                # One consistent source snapshot; never writes or migrates P1.
                rows=source.execute('SELECT seq,envelope FROM events WHERE seq>? ORDER BY seq',(seq,)).fetchall()
                for seq,text in rows:
                    e=json.loads(text);ctx=e.get('context',{});pid,resolution=resolver.resolve(ctx.get('project'))
                    task=ctx.get('task',{}).get('id') if pid else None;thread=ctx.get('thread',{}).get('id') if task else None
                    sk=hashlib.sha256(canonical([e['device_id'],e['agent'],e['session_id']]).encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?,?)',(sk,e['device_id'],e['agent'],e['session_id']))
                    if task:db.execute('INSERT OR IGNORE INTO tasks VALUES (?,?)',(pid,task))
                    if thread:db.execute('INSERT OR IGNORE INTO threads VALUES (?,?,?)',(pid,task,thread))
                    db.execute('INSERT OR IGNORE INTO work_events VALUES (?,?,?,?,?,?,?,?,?)',(e['event_id'],seq,pid,task,thread,sk,resolution,now(),text))
                    if pid:
                        for i,f in enumerate(ctx.get('facts',[])):db.execute('INSERT OR IGNORE INTO facts VALUES (?,?,?,?,?,?)',(e['event_id'],i,f['kind'],f['key'],f['state'],f['text']))
                db.execute("INSERT OR REPLACE INTO sync_state VALUES ('last_seq',?)",(str(seq),))
                db.execute("INSERT OR REPLACE INTO sync_state VALUES ('last_sync_at',?)",(now(),))
        finally:source.close()

    def visible(self,policy,project=None,task=None):
        if not active(policy):raise Rejected('reader_expired')
        if project and project not in policy.get('projects',[]):raise Rejected('project_not_found',404)
        self.sync();c=load(self.config);source=load(c['source_config']);items=[]
        with context_db(c['database']) as db:
            rows=db.execute('SELECT * FROM work_events ORDER BY source_seq').fetchall()
            sync=db.execute("SELECT value FROM sync_state WHERE key='last_sync_at'").fetchone()[0]
            for r in rows:
                if r['project_id'] not in policy.get('projects',[]) or (project and r['project_id']!=project) or (task and r['task_id']!=task):continue
                e=json.loads(r['envelope'])
                if e['device_id'] not in policy.get('source_devices',[]):continue
                device=source.get('devices',{}).get(e['device_id'],{});grant=device.get('grant',{})
                if not active(grant) or e['grant_id']!=grant.get('id') or e['policy_version']!=grant.get('policy_version'):continue
                if e['classification']!='synthetic':continue
                e['_seq']=r['source_seq'];e['_imported_at']=r['imported_at'];e['_project_id']=r['project_id'];items.append(e)
        items.sort(key=lambda e:(datetime.fromisoformat(e['occurred_at'].replace('Z','+00:00')).timestamp(),e['_seq']))
        return items,sync

    def evidence(self,e):
        return {'event_id':e['event_id'],'source_event_id':e['source_event_id'],'device_id':e['device_id'],'agent':e['agent'],
                'session_id':e['session_id'],'occurred_at':e['occurred_at'],'source_seq':e['_seq'],'imported_at':e['_imported_at']}

    def task(self,policy,project,task):
        events,sync=self.visible(policy,project,task)
        if not events:raise Rejected('task_not_found',404)
        activities=[];decisions=[];issues={};state=None;validation=None
        for e in events:
            for f in e['context']['facts']:
                fact={**f,'evidence':[self.evidence(e)],'source_assertion':True}
                if f['kind']=='activity':activities.append(fact)
                elif f['kind']=='decision':decisions.append(fact)
                elif f['kind']=='issue':issues[f['key']]=fact
                elif f['kind']=='task_status':state=fact
                elif f['kind']=='validation':validation=fact
        # Validation of an earlier task revision cannot prove a later revision.
        if state and validation:
            def order(f):
                r=f['evidence'][0]
                return (datetime.fromisoformat(r['occurred_at'].replace('Z','+00:00')).timestamp(),r['source_seq'])
            if order(validation)<order(state):
                validation={'state':'not_run','text':'Validation predates current task status',
                            'evidence':validation['evidence'],'stale':True}
        return {'project_id':project,'task_id':task,'title':events[-1]['context']['task']['title'],
                'title_evidence':[self.evidence(events[-1])],
                'current_state':state or {'state':'unknown','text':'No explicit task status','evidence':[]},
                'validation':validation or {'state':'not_run','text':'No validation evidence','evidence':[]},
                'recent_activity':activities[-20:],'decisions':decisions[-20:],
                'unresolved_issues':[x for x in issues.values() if x['state']=='open'],
                'threads':sorted({e['context']['thread']['id'] for e in events}),
                'sessions':[self.evidence(e) for e in events],'last_sync_at':sync}

    def project(self,policy,pid):
        events,sync=self.visible(policy,pid)
        c=load(self.config);p=next((p for p in c['catalog'] if p['project_id']==pid),None)
        if not p:raise Rejected('project_not_found',404)
        tasks=[self.task(policy,pid,tid) for tid in sorted({e['context']['task']['id'] for e in events})]
        completed=[t for t in tasks if t['current_state']['state']=='completed']
        pending=[t for t in tasks if t['current_state']['state']=='pending_validation' or (t['current_state']['state']=='completed' and t['validation']['state']!='passed')]
        return {'project_id':pid,'name':p['name'],'current_state':{'basis':'explicit source task assertions','tasks':[{'task_id':t['task_id'],**t['current_state']} for t in tasks]},
                'recent_activity':sorted([x for t in tasks for x in t['recent_activity']],key=lambda x:(datetime.fromisoformat(x['evidence'][0]['occurred_at'].replace('Z','+00:00')).timestamp(),x['evidence'][0]['source_seq']))[-30:],
                'completed_tasks':completed,'pending_validation_tasks':pending,
                'decisions':[x for t in tasks for x in t['decisions']],
                'unresolved_issues':[x for t in tasks for x in t['unresolved_issues']],
                'evidence':[self.evidence(e) for e in events],'last_sync_at':sync}

    def activity(self,policy,project=None,task=None):
        events,sync=self.visible(policy,project,task)
        return {'activity':[{'project_id':e['_project_id'],'task_id':e['context']['task']['id'],'facts':e['context']['facts'],'evidence':[self.evidence(e)]} for e in events[-100:]],'last_sync_at':sync}

    def search(self,policy,query):
        if not isinstance(query,str) or len(query)>200:raise Rejected('invalid_query',400)
        events,_=self.visible(policy);q=query.casefold()
        items=[{'project_id':e['_project_id'],'task_id':e['context']['task']['id'],
                'facts':e['context']['facts'],'evidence':[self.evidence(e)]} for e in reversed(events)]
        return {'matches':[x for x in items if q in json.dumps(x,ensure_ascii=False).casefold()][:30]}

    def handoff(self,policy,project,task,target_agent,target_device,token_budget):
        if not all(isinstance(x,str) and 0<len(x)<=200 for x in [project,task,target_agent,target_device]):raise Rejected('invalid_identity',400)
        if isinstance(token_budget,bool) or not isinstance(token_budget,int) or not 256<=token_budget<=policy.get('max_budget',4096):raise Rejected('invalid_token_budget',400)
        if target_agent not in policy.get('target_agents',[]) or target_device not in policy.get('target_devices',[]):raise Rejected('target_denied')
        c=load(self.config);target=c.get('targets',{}).get(target_agent+'@'+target_device)
        if not target or not active(target) or project not in target.get('projects',[]):raise Rejected('target_denied')
        combined={**policy,'projects':sorted(set(policy['projects'])&set(target['projects'])),
                  'source_devices':sorted(set(policy['source_devices'])&set(target.get('source_devices',[])))}
        t=self.task(combined,project,task)
        package={'schema_version':'workcontext.handoff.v1','project_id':project,'task_id':task,'target_agent':target_agent,'target_device':target_device,
                 'token_budget':token_budget,'budget_measure':'UTF-8 bytes upper bound; not exact tokenizer count',
                 'title':t['title'],'title_evidence':t['title_evidence'],
                 'current_state':t['current_state'],'validation':t['validation'],'last_sync_at':t['last_sync_at'],
                 'items':[],'evidence':[],'truncated':False}
        def refs(value):
            found={}
            if isinstance(value,dict):
                for r in value.get('evidence',[]):found[r['event_id']]=r
                for k,v in value.items():
                    if k!='evidence':found.update(refs(v))
            elif isinstance(value,list):
                for v in value:found.update(refs(v))
            return found
        package['evidence']=list(refs([{'evidence':t['title_evidence']},t['current_state'],t['validation']]).values())
        for kind in ['unresolved_issues','decisions','recent_activity']:
            for value in reversed(t[kind]):
                proposed=json.loads(canonical(package));proposed['items'].append({'section':kind,**value})
                proposed['evidence']=list({**refs(package),**refs(value)}.values())
                if len(canonical(proposed).encode())+64<=token_budget:package=proposed
                else:package['truncated']=True
        package['budget_upper_bound']=len(canonical(package).encode())+64
        if package['budget_upper_bound']>token_budget:raise Rejected('budget_too_small_for_evidence',422)
        return package
