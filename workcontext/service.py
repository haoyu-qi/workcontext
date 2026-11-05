"""Dependency-free central server and restricted collector; Python >=3.9.

P1 accepts a fixed synthetic fixture vocabulary only. No transcript/file reader,
remote shell, agent control, auto approval or arbitrary HTTP proxy exists here.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlsplit

VERSION = 'workcontext.event.v1'
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd')
AGENTS = ('claude_code', 'codex_cli')
FIXTURE = 'SYNTHETIC P1 fixture'
MAX_BODY = 16384


class Rejected(Exception):
    def __init__(self, reason, status=403):
        self.reason, self.status = reason, status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load(path):
    # Reload for every operation: removal/revocation does not need a restart.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as f:
        if os.name != 'nt': os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2)


def token_ok(token, expected):
    return bool(token and expected and hmac.compare_digest(digest(token), expected))


def active(grant):
    try:
        return grant['enabled'] is True and datetime.fromisoformat(grant['expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc)
    except (KeyError, ValueError, TypeError):
        return False


def check_grant(c, event=None):
    g = c.get('grant', {})
    if not active(g): raise Rejected('grant_inactive')
    if event and (event['grant_id'] != g['id'] or event['policy_version'] != g['policy_version']):
        raise Rejected('grant_mismatch')


def validate_event(e):
    if isinstance(e,dict) and e.get('schema_version')=='workcontext.event.v2':
        from .context_events import validate_v2
        try:return validate_v2(e)
        except ValueError as error:raise Rejected(str(error),400)
    fields = {'schema_version', 'event_id', 'device_id', 'agent', 'session_id', 'source_event_id',
              'event_type', 'occurred_at', 'classification', 'grant_id', 'policy_version', 'content'}
    if not isinstance(e, dict) or set(e) != fields: raise Rejected('invalid_fields', 400)
    for key in ('device_id', 'session_id', 'source_event_id', 'grant_id', 'policy_version'):
        if not isinstance(e[key], str) or not re.fullmatch(r'[A-Za-z0-9-]{1,96}', e[key]): raise Rejected('invalid_identity', 400)
    if e['schema_version'] != VERSION or e['classification'] != 'synthetic': raise Rejected('synthetic_only')
    if e['agent'] not in AGENTS or e['event_type'] not in EVENTS: raise Rejected('invalid_provider_or_event', 400)
    if not e['session_id'].startswith('p1-') or len(e['session_id'])<=3 or e['content'] != {'summary': FIXTURE}: raise Rejected('fixture_only')
    try:
        timestamp = datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None or abs((datetime.now(timezone.utc)-timestamp).total_seconds()) > 7*86400: raise ValueError()
    except (ValueError, AttributeError, TypeError): raise Rejected('invalid_time', 400)
    identity = ':'.join(e[x] for x in ('device_id', 'agent', 'session_id', 'source_event_id'))
    if e['event_id'] != digest(identity): raise Rejected('event_id_mismatch', 400)
    return e


def adapt_hook(raw, agent, c):
    allowed = {'session_id', 'hook_event_name', 'cwd', 'source_event_id', 'occurred_at', 'classification', 'prompt'}
    v2=isinstance(raw,dict) and raw.get('schema_version')=='workcontext.event.v2'
    fixture='SYNTHETIC P2 fixture' if v2 else FIXTURE
    if v2:
        allowed |= {'schema_version','context'}
        if 'context' not in raw:raise Rejected('context_required',400)
    if not isinstance(raw, dict) or set(raw)-allowed: raise Rejected('unknown_hook_fields', 400)
    if raw.get('classification') != 'synthetic' or raw.get('prompt', fixture) != fixture: raise Rejected('fixture_only')
    check_grant(c)
    cwd = raw.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute(): raise Rejected('cwd_required')
    try:
        candidate = Path(cwd).resolve(strict=True)
        roots = [Path(x).resolve(strict=True) for x in c.get('allowed_roots', [])]
    except (OSError, RuntimeError): raise Rejected('cwd_unresolvable')
    if not candidate.is_dir() or not any(candidate == r or r in candidate.parents for r in roots): raise Rejected('directory_denied')
    e = dict(schema_version='workcontext.event.v2' if v2 else VERSION, device_id=c['device_id'], agent=agent,
             session_id=raw.get('session_id'), source_event_id=raw.get('source_event_id'),
             event_type=raw.get('hook_event_name'), occurred_at=raw.get('occurred_at'),
             classification='synthetic', grant_id=c['grant']['id'], policy_version=c['grant']['policy_version'],
             content={'summary': fixture})
    if v2:e['context']=raw['context']
    if not all(isinstance(e[x], str) for x in ('session_id','source_event_id')): raise Rejected('identity_required',400)
    e['event_id'] = digest(':'.join(e[x] for x in ('device_id', 'agent', 'session_id', 'source_event_id')))
    return validate_event(e)


@contextmanager
def database(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(str(path), timeout=5)
    if os.name != 'nt': path.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL DEFAULT \'pending\', attempts INTEGER NOT NULL DEFAULT 0, error TEXT, next_attempt REAL NOT NULL DEFAULT 0)')
    try:
        with db: yield db
    finally: db.close()


def store(path, e, capacity=10000):
    text = canonical(e)
    with database(path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT envelope FROM events WHERE event_id=?',(e['event_id'],)).fetchone()
        if row:
            if row[0] != text: raise Rejected('identity_conflict',409)
            return True
        if db.execute('SELECT count(*) FROM events WHERE state=\'pending\'').fetchone()[0] >= capacity:
            raise Rejected('queue_full',503)
        db.execute('INSERT INTO events(event_id,envelope) VALUES (?,?)',(e['event_id'],text))
    return False  # context manager committed before ACK


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def http(url, token, body=None, headers=None):
    u = urlsplit(url)
    if u.username or u.password or u.fragment: raise ValueError('unsafe_destination')
    if u.scheme != 'https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):
        raise ValueError('https_required')
    request = Request(url, data=None if body is None else canonical(body).encode(),
                      headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(headers or {})})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
        return json.loads(response.read(MAX_BODY*8))


def project(e):
    # Namespaced session and stable delivery ID; no real cwd or tool contents.
    sid = 'wc-'+digest(':'.join(e[x] for x in ('device_id','agent','session_id')))[:40]
    body = {'session_id':sid, 'hook_event_name':e['event_type'],
            'workcontext_event_id':e['event_id']}
    if e['event_type']=='UserPromptSubmit': body['prompt']=FIXTURE
    if e['event_type'] in ('PreToolUse','PostToolUse'):
        body.update(tool_name='WorkContextSynthetic',tool_use_id=e['event_id'])
    return sid, body


def flush_one(config_path):
    c=load(config_path)
    with database(c['database']) as db:
        row=db.execute("SELECT event_id,envelope,attempts FROM events WHERE state='pending' ORDER BY seq LIMIT 1").fetchone()
        if not row: return False
        eid, text, attempts=row
        # FIFO preserves per-session causal order, including during backoff.
        due=db.execute('SELECT next_attempt FROM events WHERE event_id=?',(eid,)).fetchone()[0]
        if due > time.time(): return False
        e=json.loads(text)
        try:
            if c['role']=='collector':
                check_grant(c,e)
                result=http(c['central_url'].rstrip('/')+'/workcontext/v1/ingest',c['uplink_token'],e)
                if result.get('event_id') != eid or result.get('durable') is not True: raise ValueError('bad_ack')
            else:
                device=c['devices'].get(e['device_id'],{})
                check_grant(device,e)
                ap=c.get('agentpulse')
                if not ap: return False
                sid, body=project(e)
                http(ap['url']+'/api/v1/hooks',ap['ingest_token'],body,
                     {'X-Agent-Type':e['agent'],'X-AgentPulse-Delivery-Id':eid})
                # HTTP 200 is insufficient. Confirm the actual row through observe API.
                data=http(ap['url']+'/api/v1/sessions/'+sid+'/timeline?limit=200',ap['observe_token'])
                if not any(x.get('rawPayload',{}).get('workcontext_event_id')==eid for x in data.get('events',[])):
                    raise ValueError('projection_not_confirmed')
            db.execute("UPDATE events SET state='delivered',error=NULL WHERE event_id=?",(eid,))
        except Rejected:
            # Retain only the already-safe fixture. Revoked grants never transmit.
            db.execute("UPDATE events SET state='revoked',error='grant_inactive' WHERE event_id=?",(eid,))
        except Exception:
            # No exception bodies/URLs/tokens in logs or status.
            db.execute('UPDATE events SET attempts=?,error=?,next_attempt=? WHERE event_id=?',
                       (attempts+1,'delivery_unconfirmed',time.time()+min(60,2**min(attempts,6)),eid))
    return True


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self, config_path):
        self.config_path=str(config_path)
        c=load(config_path)
        if c.get('bind') != '127.0.0.1': raise ValueError('loopback_only')
        with database(c['database']): pass
        super().__init__((c['bind'],c['port']),Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def log_message(self,*args): pass  # no bodies, auth headers or paths in access logs
    def reply(self, code, data):
        raw=canonical(data).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self): self.handle_request()
    def do_POST(self): self.handle_request()
    def do_PUT(self): self.reply(403,{'error':'control_disabled'})
    def do_DELETE(self): self.reply(403,{'error':'control_disabled'})
    def do_PATCH(self): self.reply(403,{'error':'control_disabled'})
    def handle_request(self):
        self.connection.settimeout(5)
        try:
            c=load(self.server.config_path)
            path=urlsplit(self.path).path
            if self.command=='GET' and path=='/health':
                return self.reply(200,{'service':'workcontext-p1','mode':c['role'],'data':'synthetic-only','control':False})
            if self.headers.get('Origin'): raise Rejected('browser_hook_denied')
            token=self.headers.get('Authorization','').removeprefix('Bearer ')
            reader=token_ok(token,c.get('read_token_hash'))
            local=token_ok(token,c.get('local_token_hash'))
            if self.command=='GET':
                if not reader and not (c['role']=='collector' and local): raise Rejected('unauthorized',401)
                with database(c['database']) as db:
                    if path=='/workcontext/v1/status':
                        counts=dict(db.execute('SELECT state,count(*) FROM events GROUP BY state').fetchall())
                        return self.reply(200,{'counts':counts,'control':False})
                    if path=='/workcontext/v1/events' and c['role']=='server':
                        # Bounded read projection; never upstream API/config proxy.
                        rows=db.execute('SELECT envelope FROM events ORDER BY seq DESC LIMIT 100').fetchall()
                        return self.reply(200,{'events':[json.loads(r[0]) for r in rows]})
                raise Rejected('route_denied')
            if c['role']=='collector':
                if not local: raise Rejected('unauthorized',401)
                if path!='/api/v1/hooks': raise Rejected('route_denied')
            else:
                device=next((v for v in c.get('devices',{}).values() if token_ok(token,v.get('token_hash'))),None)
                if not device: raise Rejected('unauthorized',401)
                if path!='/workcontext/v1/ingest': raise Rejected('route_denied')
                check_grant(device)
            if self.headers.get('Transfer-Encoding'): raise Rejected('invalid_body',400)
            try: length=int(self.headers.get('Content-Length','0'))
            except ValueError: raise Rejected('invalid_body',400)
            if not 0 < length <= MAX_BODY: raise Rejected('body_size',413)
            try: raw=json.loads(self.rfile.read(length))
            except (ValueError,UnicodeError): raise Rejected('invalid_json',400)
            if c['role']=='collector': e=adapt_hook(raw,self.headers.get('X-Agent-Type'),c)
            else:
                e=validate_event(raw)
                if e['device_id'] != device['device_id']: raise Rejected('device_mismatch')
                check_grant(device,e)
            duplicate=store(c['database'],e,c.get('capacity',10000))
            self.reply(200,{'event_id':e['event_id'],'duplicate':duplicate,'durable':True})
        except Rejected as e: self.reply(e.status,{'error':e.reason})
        except Exception: self.reply(503,{'error':'unavailable'})


def run(config):
    server=Server(config); done=threading.Event()
    def worker():
        while not done.wait(.25):
            try: flush_one(config)
            except Exception: pass  # invalid config fails closed; health/config check before deployment
    thread=threading.Thread(target=worker,daemon=True); thread.start()
    # Signal handler must not call shutdown() on serve_forever's own thread.
    def stop(*_):
        done.set(); threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try: server.serve_forever(poll_interval=.2)
    finally: done.set(); server.server_close(); thread.join(timeout=7)


def init(directory, role, device, url):
    directory=Path(directory).resolve()
    if (directory/'config.json').exists(): raise ValueError('config_exists')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    token=secrets.token_urlsafe(32)
    c={'role':role,'bind':'127.0.0.1','port':43110 if role=='server' else 43120,
       'database':str(directory/'events.sqlite'),'capacity':10000}
    private_write(directory/'read-token.json',{'token':token}); c['read_token_hash']=digest(token)
    if role=='server': c['devices']={}
    else:
        c.update(device_id=device,central_url=url,uplink_token=secrets.token_urlsafe(32))
        c['local_token_hash']=digest(token)
        fixture=directory/'synthetic-fixture';fixture.mkdir(mode=0o700,exist_ok=True)
        c['allowed_roots']=[str(fixture)]
        c['grant']={'id':'p1-synthetic','policy_version':'p1-v1','enabled':True,
                    'expires_at':(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()}
    private_write(directory/'config.json',c)
    print('Created private config:',directory/'config.json')


def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('serve');s.add_argument('--config',required=True)
    s=sub.add_parser('init');s.add_argument('--directory',required=True);s.add_argument('--role',choices=['server','collector'],required=True)
    s.add_argument('--device',default='macbook');s.add_argument('--central-url',default='https://pending.example.invalid:8443')
    args=parser.parse_args()
    if args.command=='serve': run(args.config)
    else: init(args.directory,args.role,args.device,args.central_url)


if __name__=='__main__': main()
