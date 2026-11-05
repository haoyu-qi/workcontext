"""Local-only normalized Agent log archive, including Zstandard and Hermes SQLite."""
import hashlib,io,json,os,re,shutil,sqlite3,subprocess,time
from contextlib import contextmanager
from pathlib import Path
from .service import canonical

SENSITIVE=re.compile(r'(?i)^(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|credentials|reasoning|reasoning_content|reasoning_details)$')
SECRETS=re.compile(r'(?:-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._~+/-]{16,})')
def scrub(value):
    if isinstance(value,dict):return {k:scrub(v) for k,v in value.items() if not SENSITIVE.fullmatch(k)}
    if isinstance(value,list):return [scrub(x) for x in value]
    return SECRETS.sub('[REDACTED]',value) if isinstance(value,str) else value

@contextmanager
def lines(path):
    if path.suffix not in ['.zstd','.zst']:
        with path.open(encoding='utf-8') as f:yield f
        return
    executable=shutil.which('zstd')
    if executable:
        child=subprocess.Popen([executable,'-dc','--',str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        stream=io.TextIOWrapper(child.stdout,encoding='utf-8')
        try:
            yield stream
            stream.close()
            if child.wait(timeout=10)!=0:raise ValueError('zstd_decode_failed')
        finally:
            stream.close()
            if child.poll() is None:child.kill();child.wait()
    else:
        try:from compression import zstd
        except ImportError:
            try:import zstandard as zstd
            except ImportError:raise ValueError('zstd_decoder_unavailable')
        if hasattr(zstd,'open'):
            with zstd.open(path,'rt',encoding='utf-8') as f:yield f
        else:
            with path.open('rb') as raw,zstd.ZstdDecompressor().stream_reader(raw) as decoder,io.TextIOWrapper(decoder,encoding='utf-8') as f:yield f

def inventory(sources):
    result=[]
    for s in sources:
        p=Path(s['path']).expanduser();files=eligible(p,s.get('format','jsonl'));extensions={}
        for f in files:extensions[f.suffix]=extensions.get(f.suffix,0)+1
        result.append({'agent':s['agent'],'path':str(p),'exists':p.exists(),'files':len(files),'extensions':extensions,'format':s.get('format','jsonl')})
    return result

def eligible(root,format):
    if root.is_file():return [root]
    if not root.exists():return []
    return sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink() and any(str(p).endswith(x) for x in ['.jsonl','.jsonl.zstd','.jsonl.zst']))

def records(path,format):
    if format=='hermes-sqlite':
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:
            columns={r[1] for r in db.execute('PRAGMA table_info(messages)')}
            allowed=[k for k in ['id','session_id','role','content','tool_name','tool_call_id','timestamp'] if k in columns]
            if not {'id','session_id'}<=columns:raise ValueError('unsupported_hermes_schema')
            for row in db.execute('SELECT '+','.join(allowed)+' FROM messages ORDER BY id'):
                yield row['id'],dict(row)
        finally:db.close()
    else:
        with lines(path) as f:
            for number,line in enumerate(f,1):
                if len(line)>1048576:raise ValueError('record_exceeds_limit')
                if line.strip():yield number,json.loads(line)

def collect(config,include_content=False,max_records=None):
    root=Path(config['database']).expanduser();root.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(str(root));db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    if os.name!='nt':root.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS source_records (id TEXT PRIMARY KEY,agent TEXT,session TEXT,kind TEXT,source_path TEXT,source_ordinal TEXT,body TEXT,collected REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY,signature TEXT)')
    summary={'inserted':0,'updated':0,'duplicates':0,'unchanged_files':0,'errors':[],'content_included':include_content};seen=0
    try:
        for source in config['sources']:
            path=Path(source['path']).expanduser()
            for p in eligible(path,source.get('format','jsonl')):
                try:
                    stat=p.stat();signature=canonical([stat.st_size,stat.st_mtime_ns,include_content])
                    previous=db.execute('SELECT signature FROM files WHERE path=?',(str(p),)).fetchone()
                    if previous and previous[0]==signature:summary['unchanged_files']+=1;continue
                    session=p.stem
                    for ordinal,raw in records(p,source.get('format','jsonl')):
                        if not isinstance(raw,dict):continue
                        payload=raw.get('payload',{});payload=payload if isinstance(payload,dict) else {}
                        if raw.get('type') in ['session','session_meta']:session=raw.get('id') or payload.get('id') or session
                        session=str(raw.get('session_id') or raw.get('sessionId') or session)
                        event=raw.get('event') if isinstance(raw.get('event'),dict) else {}
                        body={'kind':str(raw.get('type') or raw.get('role') or event.get('type') or 'unknown'),'timestamp':raw.get('timestamp',raw.get('createdAt')),'tool_name':raw.get('tool_name') or payload.get('name')}
                        if include_content:body['record']=scrub(raw)
                        # Logical session + ordinal + digest deduplicates archived/compressed twins.
                        identity=canonical([source['agent'],session,str(ordinal),hashlib.sha256(canonical(raw).encode()).hexdigest()])
                        eid=hashlib.sha256(identity.encode()).hexdigest();before=db.total_changes
                        db.execute('INSERT OR IGNORE INTO source_records VALUES (?,?,?,?,?,?,?,?)',(eid,source['agent'],session,body['kind'],str(p),str(ordinal),canonical(body),time.time()))
                        if db.total_changes>before:summary['inserted']+=1
                        elif include_content and 'record' not in json.loads(db.execute('SELECT body FROM source_records WHERE id=?',(eid,)).fetchone()[0]):
                            db.execute('UPDATE source_records SET body=? WHERE id=?',(canonical(body),eid));summary['updated']+=1
                        else:summary['duplicates']+=1
                        seen+=1
                        if max_records and seen>=max_records:db.commit();return summary
                    db.execute('INSERT OR REPLACE INTO files VALUES (?,?)',(str(p),signature));db.commit()
                except Exception as e:
                    db.commit();summary['errors'].append({'agent':source['agent'],'file':str(p),'reason':str(e) if isinstance(e,ValueError) and str(e) in ['zstd_decoder_unavailable','zstd_decode_failed','unsupported_hermes_schema','record_exceeds_limit'] else type(e).__name__})
        return summary
    finally:db.commit();db.close()
