"""Polling worker: local command registry, immutable project revision, durable result outbox."""
import argparse,hashlib,json,os,platform,signal,sqlite3,subprocess,tempfile,threading,time
from pathlib import Path
from contextlib import closing
from .service import http,load,canonical

def host_os():return {'Darwin':'macos','Windows':'windows','Linux':'linux'}.get(platform.system(),'unknown')
def revision(project):
    root=Path(project['root']).resolve();manifest=load(project['manifest'])
    for name,expected in manifest['files'].items():
        p=(root/name).resolve()
        if root not in p.parents or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:return 'unavailable'
    raw=canonical(manifest['files']).encode()
    return 'sha256-'+hashlib.sha256(raw).hexdigest()

def post(c,path,body):return http(c['url'].rstrip('/')+path,load(c['token_file'])['token'],body)

def journal(c):
    p=Path(c['journal']);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(p),timeout=10);db.row_factory=sqlite3.Row
    if os.name!='nt':p.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, phase TEXT, result TEXT)');db.commit();return db

def base_result(job,state,code=None,stdout='',stderr='',duration=0,truncated=False):
    stdout_bytes=stdout.encode();stderr_bytes=stderr.encode();truncated=truncated or len(stdout_bytes)>8192 or len(stderr_bytes)>8192
    stdout=stdout_bytes[:8192].decode('utf-8','ignore');stderr=stderr_bytes[:8192].decode('utf-8','ignore')
    b=job['request'];return {'state':state,'exit_code':code,'stdout':stdout,'stderr':stderr,'duration':duration,'revision':b['revision'],'os':host_os(),'capability':b['capability'],'truncated':truncated}

def terminate(child):
    if child.poll() is not None:return
    try:
        if os.name=='nt':subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=5)
        else:os.killpg(child.pid,signal.SIGKILL)
    except (ProcessLookupError,subprocess.TimeoutExpired):pass
    try:child.wait(timeout=5)
    except subprocess.TimeoutExpired:child.kill();child.wait()

def execute(c,job):
    b=job['request'];cap=c['capabilities'].get(b['capability']);project=c['projects'].get(b['project'])
    if not cap or not project or cap['project']!=b['project'] or revision(project)!=b['revision'] or b['required_os'] not in ['any',host_os()] or (b.get('workspace_node') and b['workspace_node']!=c['node']):
        return base_result(job,'failed',stderr='Local capability, OS, workspace or revision verification failed')
    timeout=cap['timeout']
    if not isinstance(timeout,(int,float)) or not 0<timeout<=3600 or not isinstance(cap['argv'],list) or not all(isinstance(x,str) for x in cap['argv']):return base_result(job,'failed',stderr='Invalid local capability definition')
    root=Path(project['root']).resolve();started=time.monotonic()
    private=Path(c['journal']).parent/'runtime'/job['attempt'];private.mkdir(parents=True,exist_ok=True,mode=0o700)
    env={'PATH':c.get('path',os.defpath),'HOME':str(private),'USERPROFILE':str(private),'TMPDIR':str(private),'TMP':str(private),'TEMP':str(private),'LANG':'en_US.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONPYCACHEPREFIX':str(private/'pycache')}
    if os.name=='nt':
        for k in ['SystemRoot','WINDIR','COMSPEC']:
            if k in os.environ:env[k]=os.environ[k]
    options={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
    child=subprocess.Popen(cap['argv'],cwd=str(root),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,**options)
    buffers=[bytearray(),bytearray()];truncated=[False,False]
    def drain(stream,index):
        while True:
            chunk=stream.read(4096)
            if not chunk:break
            remaining=8192-len(buffers[index]);buffers[index].extend(chunk[:remaining])
            if len(chunk)>remaining:truncated[index]=True
    threads=[threading.Thread(target=drain,args=(child.stdout,0),daemon=True),threading.Thread(target=drain,args=(child.stderr,1),daemon=True)]
    for t in threads:t.start()
    state=None;last=0
    try:
        while child.poll() is None:
            now=time.monotonic()
            if now-started>=timeout:state='timed_out';terminate(child);break
            if now-last>2:
                try:
                    result=post(c,'/progress',{'id':job['id'],'attempt':job['attempt']})
                    if result['state']=='cancel_requested':state='cancelled';terminate(child);break
                except Exception:pass
                last=now
            time.sleep(.1)
        state=state or ('succeeded' if child.returncode==0 else 'failed')
    finally:
        if child.poll() is None:terminate(child)
        for t in threads:t.join(timeout=3)
        child.stdout.close();child.stderr.close()
    return base_result(job,state,child.returncode,buffers[0].decode('utf-8','replace'),buffers[1].decode('utf-8','replace'),round(time.monotonic()-started,3),any(truncated))

def step(config):
    c=load(config);report={'os':host_os(),'capabilities':list(c['capabilities']),'projects':{k:revision(p) for k,p in c['projects'].items()}}
    post(c,'/heartbeat',report)
    # A finished result is retried before asking for new work.
    with closing(journal(c)) as db, db:
        rows=db.execute("SELECT * FROM attempts WHERE phase='result_pending'").fetchall()
        for row in rows:
            payload=json.loads(row['result']);ack=post(c,'/result',payload)
            if ack.get('durable'):db.execute("UPDATE attempts SET phase='reported' WHERE id=?",(row['id'],));db.commit()
        job=post(c,'/claim',{})['job']
        if not job:return False
        old=db.execute('SELECT * FROM attempts WHERE id=?',(job['attempt'],)).fetchone()
        if old and old['phase']=='reported':return False
        if job['state']=='accepted':
            # Mark locally before remote start; a crash after start is never silently retried.
            db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,NULL)',(job['attempt'],'starting'));db.commit()
            post(c,'/start',{'id':job['id'],'attempt':job['attempt']})
            db.execute("UPDATE attempts SET phase='running' WHERE id=?",(job['attempt'],));db.commit()
            try:result=execute(c,job)
            except Exception:result=base_result(job,'failed',stderr='Local execution could not complete')
        elif old and old['phase']=='result_pending':return False
        else:result=base_result(job,'interrupted',stderr='Worker restarted or lost local execution state; execution was not retried')
        payload={'id':job['id'],'attempt':job['attempt'],'result':result}
        db.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)',(job['attempt'],'result_pending',canonical(payload)));db.commit()
        if post(c,'/result',payload).get('durable'):db.execute("UPDATE attempts SET phase='reported' WHERE id=?",(job['attempt'],));db.commit()
        return True

def run(config,once=False):
    # One process per journal prevents duplicate local execution after repeated starts.
    def interrupted(*_):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    c=load(config);lockpath=Path(c['journal']).with_suffix('.lock');lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                step(config)
                status={'status':'connected','at':time.time()}
            except Exception as error:
                if once:raise
                status={'status':'request_failed','at':time.time(),'error_type':type(error).__name__}
            statusfile=Path(c['journal']).parent/'worker-health.json'
            statusfile.write_text(json.dumps(status),encoding='utf-8')
            if os.name!='nt':statusfile.chmod(0o600)
            if once:break
            time.sleep(load(config).get('poll_seconds',3))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--once',action='store_true');a=p.parse_args();run(a.config,a.once)
