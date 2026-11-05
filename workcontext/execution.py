"""Independent capability-based scheduler. No shell or arbitrary command endpoint."""
import argparse,hashlib,json,secrets,signal,sqlite3,threading,time,uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .service import load,canonical,token_ok,active,Rejected

IDENTITY=__import__('re').compile(r'[A-Za-z0-9._-]{1,128}')
TERMINAL={'succeeded','failed','cancelled','timed_out','interrupted'}
@contextmanager
def database(path):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(p),timeout=10);db.row_factory=sqlite3.Row
    if __import__('os').name!='nt':p.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, heartbeat REAL, report TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT, idem TEXT, request TEXT, state TEXT, node TEXT, attempt TEXT, created REAL, updated REAL, result TEXT, UNIQUE(owner,idem))')
    try:
        with db:yield db
    finally:db.close()

def validate_job(b):
    required={'project','revision','capability','required_os','idempotency_key'}
    if not isinstance(b,dict) or not required<=set(b) or set(b)-required-{'workspace_node','preferred_node','allowed_nodes'}:raise Rejected('invalid_fields',400)
    for key in required-{'required_os'}:
        if not isinstance(b[key],str) or not IDENTITY.fullmatch(b[key]):raise Rejected('invalid_identity',400)
    if b['required_os'] not in ['any','windows','macos','linux']:raise Rejected('invalid_os',400)
    for key in ['workspace_node','preferred_node']:
        if key in b and (not isinstance(b[key],str) or not IDENTITY.fullmatch(b[key])):raise Rejected('invalid_node',400)
    if 'allowed_nodes' in b and (not isinstance(b['allowed_nodes'],list) or not b['allowed_nodes'] or len(b['allowed_nodes'])>20 or any(not isinstance(n,str) or not IDENTITY.fullmatch(n) for n in b['allowed_nodes'])):raise Rejected('invalid_nodes',400)

class Scheduler:
    def __init__(self,config):self.config=config
    def policy(self,token):
        c=load(self.config)
        for role,group in [('operator',c.get('operators',{})),('reader',c.get('readers',{})),('worker',c.get('nodes',{}))]:
            for name,p in group.items():
                if token_ok(token,p.get('token_hash')) and active(p):return role,name,p
        raise Rejected('unauthorized',401)
    def tick(self,db,c):
        cutoff=time.time()-c.get('lease_seconds',90)
        db.execute("UPDATE jobs SET state='recovery_required',updated=? WHERE state IN ('accepted','running','cancel_requested') AND updated<?",(time.time(),cutoff))
    def public(self,row,full=False):
        v=dict(row);v['request']=json.loads(v['request']);v.pop('owner');v.pop('idem')
        if v['result']:
            v['result']=json.loads(v['result'])
            if not full:v['result']={k:x for k,x in v['result'].items() if k not in ['stdout','stderr']}
        return v
    def candidates(self,db,c,b):
        reports={r['id']:r for r in db.execute('SELECT * FROM nodes')};out=[];excluded={}
        for name,n in c['nodes'].items():
            reason=None;r=reports.get(name);report=json.loads(r['report']) if r else {}
            if not active(n):reason='disabled'
            elif b.get('workspace_node') and b['workspace_node']!=name:reason='workspace_location'
            elif b.get('allowed_nodes') and name not in b['allowed_nodes']:reason='not_allowed'
            elif b['required_os']!='any' and b['required_os']!=n['os']:reason='os_mismatch'
            elif b['capability'] not in n.get('capabilities',[]):reason='capability_missing'
            elif b['project'] not in n.get('projects',[]):reason='project_unavailable'
            elif not r or time.time()-r['heartbeat']>c.get('heartbeat_seconds',30):reason='worker_offline'
            elif report.get('os')!=n['os']:reason='reported_os_mismatch'
            elif b['capability'] not in report.get('capabilities',[]):reason='capability_unavailable'
            elif report.get('projects',{}).get(b['project'])!=b['revision']:reason='revision_unavailable'
            else:
                occupied=db.execute("SELECT count(*) FROM jobs WHERE node=? AND state IN ('accepted','running','cancel_requested','recovery_required')",(name,)).fetchone()[0]
                if occupied>=n.get('capacity',1):reason='busy_or_recovery_required'
            if reason:excluded[name]=reason
            else:out.append((0 if b.get('preferred_node')==name else 1,n.get('priority',100),name))
        out.sort();return [x[2] for x in out],excluded
    def operate(self,role,name,policy,method,path,body=None):
        c=load(self.config);u=urlsplit(path);q=parse_qs(u.query)
        if u.path!='/job' and u.query:raise Rejected('invalid_query',400)
        with database(c['database']) as db:
            db.execute('BEGIN IMMEDIATE');self.tick(db,c)
            if role in ['reader','operator']:
                if method=='GET' and u.path=='/state':
                    nodes=[]
                    for node,n in c['nodes'].items():
                        r=db.execute('SELECT * FROM nodes WHERE id=?',(node,)).fetchone()
                        nodes.append({'node':node,'os':n['os'],'capabilities':n['capabilities'],'projects':n['projects'],'enabled':active(n),'online':bool(active(n) and r and time.time()-r['heartbeat']<=c.get('heartbeat_seconds',30)),'last_heartbeat':r['heartbeat'] if r else None,'report':json.loads(r['report']) if r else None})
                    jobs=[]
                    for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100'):
                        b=json.loads(r['request'])
                        if b['project'] not in policy.get('projects',[]):continue
                        value=self.public(r)
                        if r['state']=='queued':value['eligible_nodes'],value['waiting_reasons']=self.candidates(db,c,b)
                        jobs.append(value)
                    return {'nodes':[n for n in nodes if set(n['projects'])&set(policy.get('projects',[]))], 'jobs':jobs,'read_only':role=='reader'}
                if method=='GET' and u.path=='/job' and set(q)=={'id'} and len(q['id'])==1:
                    r=db.execute('SELECT * FROM jobs WHERE id=?',(q['id'][0],)).fetchone()
                    if not r or json.loads(r['request'])['project'] not in policy.get('projects',[]):raise Rejected('job_not_found',404)
                    return self.public(r,True)
                if role!='operator':raise Rejected('read_only')
                if method=='POST' and u.path=='/submit':
                    validate_job(body)
                    if body['project'] not in policy.get('projects',[]) or body['capability'] not in policy.get('capabilities',[]):raise Rejected('task_scope_denied')
                    if body.get('workspace_node') and body['workspace_node'] not in c['nodes']:raise Rejected('unknown_node',400)
                    if body.get('preferred_node') and body['preferred_node'] not in c['nodes']:raise Rejected('unknown_node',400)
                    if any(x not in c['nodes'] for x in body.get('allowed_nodes',[])):raise Rejected('unknown_node',400)
                    old=db.execute('SELECT * FROM jobs WHERE owner=? AND idem=?',(name,body['idempotency_key'])).fetchone()
                    raw=canonical(body)
                    if old:
                        if old['request']!=raw:raise Rejected('idempotency_conflict',409)
                        return self.public(old)
                    job=uuid.uuid4().hex;stamp=time.time()
                    db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(job,name,body['idempotency_key'],raw,'queued',None,None,stamp,stamp,None))
                    return self.public(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
                if method=='POST' and u.path=='/cancel' and isinstance(body,dict) and set(body)=={'id'}:
                    r=db.execute('SELECT * FROM jobs WHERE id=?',(body['id'],)).fetchone()
                    if not r or json.loads(r['request'])['project'] not in policy.get('projects',[]):raise Rejected('job_not_found',404)
                    if r['state']=='queued' or r['state']=='accepted':state='cancelled'
                    elif r['state'] in ['running','recovery_required']:state='cancel_requested'
                    else:state=r['state']
                    db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,time.time(),r['id']));return {'id':r['id'],'state':state}
            elif role=='worker' and method=='POST' and not u.query:
                if u.path=='/heartbeat':
                    if not isinstance(body,dict) or set(body)!={'os','capabilities','projects'} or body['os']!=policy['os'] or not isinstance(body['capabilities'],list) or not isinstance(body['projects'],dict):raise Rejected('invalid_report',400)
                    if set(body['capabilities'])-set(policy.get('capabilities',[])) or set(body['projects'])-set(policy.get('projects',[])):raise Rejected('report_scope_denied')
                    db.execute('INSERT OR REPLACE INTO nodes VALUES (?,?,?)',(name,time.time(),canonical(body)))
                    return {'node':name,'heartbeat':time.time()}
                if u.path=='/claim' and body=={}:
                    previous=db.execute("SELECT * FROM jobs WHERE node=? AND state IN ('accepted','running','cancel_requested','recovery_required') ORDER BY created LIMIT 1",(name,)).fetchone()
                    if previous:return {'job':self.public(previous)}
                    for row in db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created").fetchall():
                        eligible,_=self.candidates(db,c,json.loads(row['request']))
                        if eligible and eligible[0]==name:
                            attempt=uuid.uuid4().hex
                            db.execute("UPDATE jobs SET state='accepted',node=?,attempt=?,updated=? WHERE id=? AND state='queued'",(name,attempt,time.time(),row['id']))
                            return {'job':self.public(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())}
                    return {'job':None}
                if u.path in ['/start','/progress','/result']:
                    if not isinstance(body,dict) or not {'id','attempt'}<=set(body):raise Rejected('invalid_fields',400)
                    row=db.execute('SELECT * FROM jobs WHERE id=?',(body['id'],)).fetchone()
                    if not row or row['node']!=name or row['attempt']!=body['attempt']:raise Rejected('attempt_denied')
                    if u.path=='/start':
                        if set(body)!={'id','attempt'} or row['state']!='accepted':raise Rejected('start_denied',409)
                        db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),row['id']));return {'state':'running'}
                    if u.path=='/progress':
                        if set(body)!={'id','attempt'}:raise Rejected('invalid_fields',400)
                        state='running' if row['state']=='recovery_required' else row['state']
                        if state in ['running','cancel_requested']:
                            db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,time.time(),row['id']))
                            db.execute('UPDATE nodes SET heartbeat=? WHERE id=?',(time.time(),name))
                        return {'state':state}
                    if set(body)!={'id','attempt','result'}:raise Rejected('invalid_fields',400)
                    value=body['result'];fields={'state','exit_code','stdout','stderr','duration','revision','os','capability','truncated'}
                    if not isinstance(value,dict) or set(value)!=fields or value['state'] not in TERMINAL:raise Rejected('invalid_result',400)
                    if any(not isinstance(value[k],str) or len(value[k].encode())>32768 for k in ['stdout','stderr']):raise Rejected('output_limit',400)
                    req=json.loads(row['request'])
                    if value['revision']!=req['revision'] or value['capability']!=req['capability'] or value['os']!=policy['os']:raise Rejected('result_identity_mismatch',400)
                    if value['state']=='succeeded' and (type(value['exit_code']) is not int or value['exit_code']!=0):raise Rejected('exit_code_mismatch',400)
                    text=canonical(value)
                    if row['result']:
                        if text!=row['result']:raise Rejected('result_conflict',409)
                        return {'durable':True,'duplicate':True}
                    if row['state'] not in ['running','cancel_requested','recovery_required']:raise Rejected('result_state_denied',409)
                    db.execute('UPDATE jobs SET result=?,state=?,updated=? WHERE id=?',(text,value['state'],time.time(),row['id']))
                    return {'durable':True,'duplicate':False}
            raise Rejected('route_denied')

class LoopbackServer(ThreadingHTTPServer):
    # HTTPServer.server_bind otherwise performs a reverse-DNS lookup even for
    # 127.0.0.1, which can stall startup on hosts with an unavailable resolver.
    def server_bind(self):
        from socketserver import TCPServer
        TCPServer.server_bind(self)
        self.server_name='localhost'
        self.server_port=self.server_address[1]

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def reply(self,code,body):
        raw=canonical(body).encode();self.send_response(code)
        for k,v in {'Content-Type':'application/json','Content-Length':str(len(raw)),'Cache-Control':'no-store'}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_PUT(self):self.reply(403,{'error':'route_denied'})
    do_DELETE=do_PUT
    do_PATCH=do_PUT
    def dispatch(self):
        self.connection.settimeout(10)
        try:
            if self.command=='GET' and self.path=='/health':return self.reply(200,{'service':'workcontext-execution','capability_routing':True})
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):raise Rejected('origin_or_encoding_denied')
            s=Scheduler(self.server.config);role,name,p=s.policy(self.headers.get('Authorization','').removeprefix('Bearer '))
            body=None
            if self.command=='POST':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=131072:raise Rejected('body_size',413)
                body=json.loads(self.rfile.read(length))
            self.reply(200,s.operate(role,name,p,self.command,self.path,body))
        except Rejected as e:self.reply(e.status,{'error':e.reason})
        except (ValueError,TypeError,KeyError):self.reply(400,{'error':'invalid_request'})
        except Exception:self.reply(503,{'error':'execution_unavailable'})

def serve(config):
    c=load(config)
    if c['bind']!='127.0.0.1':raise ValueError('loopback_only')
    with database(c['database']):pass
    s=LoopbackServer((c['bind'],c['port']),Handler);s.daemon_threads=True;s.config=config
    def stop(*_):threading.Thread(target=s.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:s.serve_forever(.2)
    finally:s.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);serve(p.parse_args().config)
