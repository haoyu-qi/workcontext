"""Incremental, scoped MacBook work-log relay with a durable private outbox."""
import argparse,hashlib,json,os,sqlite3,time
from contextlib import closing
from pathlib import Path
from .service import canonical,load
from .source_logs import eligible,records
from .long_memory import clean
from .memory_client import api

IGNORE={'reasoning','reasoning_content','reasoning_details','encrypted_content','system_prompt','api_content','arguments','output','tool_response','tool_input','tool_calls'}
def text_from(raw):
    parts=[]
    def walk(value,depth=0):
        if depth>9:return
        if isinstance(value,dict):
            role=value.get('role');kind=str(value.get('type','')).lower()
            if role in ['system','developer','tool'] or value.get('channel')=='analysis' or 'reasoning' in kind or 'analysis' in kind or kind in ['function_call_output','tool_result']:return
            for key,v in value.items():
                if key in IGNORE:continue
                if isinstance(v,str) and key in ['text','content','message','response','summary'] and v.strip():parts.append(v)
                elif isinstance(v,(dict,list)):walk(v,depth+1)
        elif isinstance(value,list):
            for v in value:walk(v,depth+1)
    walk(raw);text=clean('\n'.join(dict.fromkeys(parts)))
    if not text:
        name=raw.get('tool_name') or (raw.get('payload') or {}).get('name') if isinstance(raw.get('payload',{}),dict) else raw.get('tool_name')
        if name:text='Tool activity: '+str(name)
    return text[:8000]

def permitted(c,cwd):
    if not isinstance(cwd,str) or not Path(cwd).is_absolute():return False
    p=Path(cwd).resolve()
    return any(p==Path(root).expanduser().resolve() or Path(root).expanduser().resolve() in p.parents for root in c['allowed_roots'])
def connect(c):
    path=Path(c['outbox']);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    if os.name!='nt':path.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS checkpoints (path TEXT PRIMARY KEY, signature TEXT, ordinal INTEGER, session TEXT, cwd TEXT, complete INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, body TEXT, state TEXT DEFAULT \'pending\')');db.commit();return db

def scan(config):
    c=load(config) if not isinstance(config,dict) else config;count=0;errors=[]
    with closing(connect(c)) as db,db:
        for source in c['sources']:
            root=Path(source['path']).expanduser();candidates=sorted(eligible(root,source.get('format','jsonl')),key=lambda p:p.stat().st_mtime,reverse=True);processed=0
            session_dirs={}
            if source.get('format')=='hermes-sqlite' and root.exists():
                with closing(sqlite3.connect(root.resolve().as_uri()+'?mode=ro',uri=True)) as native:
                    session_dirs=dict(native.execute('SELECT id,cwd FROM sessions'))
            for path in candidates:
                stat=path.stat();signature=str(stat.st_size)+':'+str(stat.st_mtime_ns);old=db.execute('SELECT * FROM checkpoints WHERE path=?',(str(path),)).fetchone()
                if old and old['signature']==signature and old['complete']:continue
                if processed>=c.get('files_per_source',2):break
                processed+=1;last=old['ordinal'] if old else 0;session=old['session'] if old else path.stem;cwd=old['cwd'] if old else None;seen=0;added=0;complete=1
                try:
                    for ordinal,raw in records(path,source.get('format','jsonl'),skip_oversized=True):
                        if not isinstance(raw,dict):continue
                        seen=int(ordinal);payload=raw.get('payload') if isinstance(raw.get('payload'),dict) else {}
                        if raw.get('type') in ['session','session_meta']:
                            session=raw.get('id') or payload.get('id') or session;cwd=raw.get('cwd') or payload.get('cwd') or cwd
                        session=raw.get('session_id') or raw.get('sessionId') or session
                        cwd=raw.get('cwd') or payload.get('cwd') or session_dirs.get(session) or cwd
                        if seen<=last:continue
                        text=text_from(raw)
                        if text and permitted(c,cwd):
                            identity=hashlib.sha256(canonical([c['device'],source['agent'],session,str(ordinal),hashlib.sha256(canonical(raw).encode()).hexdigest()]).encode()).hexdigest()
                            row={'id':identity,'device':c['device'],'agent':source['agent'],'session':str(session),'project':Path(cwd).name,'ordinal':str(ordinal),'text':text,'occurred':str(raw.get('timestamp',raw.get('createdAt',''))),'metadata':{'cwd':str(Path(cwd).resolve()),'source_path':str(path),'record_ordinal':str(ordinal)}}
                            before=db.total_changes;db.execute('INSERT OR IGNORE INTO outbox(id,body) VALUES (?,?)',(identity,canonical(row)));count+=db.total_changes-before
                        added+=1
                        if added>=c.get('records_per_file',500):complete=0;break
                    if seen<last:seen=0;complete=0
                    db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?)',(str(path),signature,seen,str(session),cwd,complete));db.commit()
                except Exception as error:db.commit();errors.append({'source':source['agent'],'error':type(error).__name__})
    return {'queued':count,'errors':errors}

def upload(config):
    c=load(config) if not isinstance(config,dict) else config
    with closing(connect(c)) as db,db:
        batch=[]
        size=0
        for row in db.execute("SELECT id,body FROM outbox WHERE state='pending' ORDER BY rowid LIMIT 100"):
            r=json.loads(row['body'])
            if not permitted(c,r['metadata'].get('cwd')):db.execute("UPDATE outbox SET state='blocked' WHERE id=?",(row['id'],));continue
            encoded=len(canonical(r).encode())+1
            if size+encoded>900000:break
            batch.append(r);size+=encoded
        if not batch:return {'delivered':0}
        result=api(c,'/sources/ingest',{'records':batch})
        if not result.get('durable') or set(result.get('ids',[]))!={r['id'] for r in batch}:raise ValueError('durable_ack_missing')
        for r in batch:db.execute("UPDATE outbox SET state='delivered' WHERE id=?",(r['id'],))
        return {'delivered':len(batch)}

def run(config,once=False):
    c=load(config);statusfile=Path(c['outbox']).parent/'relay-health.json'
    while True:
        status={'at':time.time()}
        try:
            status.update(scan(config));status['delivered']=0
            for _ in range(c.get('batches_per_cycle',10)):
                delivered=upload(config)['delivered'];status['delivered']+=delivered
                if not delivered:break
            status['state']='connected'
        except Exception as error:status.update(state='retrying',error=type(error).__name__)
        with closing(connect(c)) as db:status['queue']=dict(db.execute('SELECT state,count(*) FROM outbox GROUP BY state'))
        statusfile.write_text(json.dumps(status,indent=2));statusfile.chmod(0o600)
        if once:return status
        time.sleep(c.get('interval',15))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--once',action='store_true');a=p.parse_args();print(json.dumps(run(a.config,a.once)))
