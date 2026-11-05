"""Actual process/HTTP/SQLite tests. This is a Mac test, never Windows evidence."""
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workcontext.service import adapt_hook,canonical,digest,now,private_write,validate_event,Rejected,FIXTURE,init


def port():
    with socket.socket() as s: s.bind(('127.0.0.1',0));return s.getsockname()[1]


def request(base,path,token='',body=None,method=None,headers=None):
    req=Request(base+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(headers or {})},
                data=None if body is None else json.dumps(body).encode(),method=method)
    try:
        with urlopen(req,timeout=5) as r: return r.status,json.loads(r.read())
    except HTTPError as e: return e.code,json.loads(e.read())


def wait(fn,timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=fn()
            if value:return value
        except (OSError,KeyError,ValueError):pass
        time.sleep(.15)
    raise AssertionError('condition timed out')


class P1Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent=ROOT/'.p1/runs';parent.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='acceptance-',dir=parent))
        cls.ap_port=port();cls.ap_url='http://127.0.0.1:'+str(cls.ap_port)
        cls.ap_log=open(cls.root/'agentpulse.log','wb')
        cls.env={k:v for k,v in os.environ.items() if k in ('PATH','TMPDIR','LANG')}
        home=cls.root/'ap-home';home.mkdir()
        cls.env.update(HOME=str(home),CODEX_HOME=str(home/'.codex'),DATA_DIR=str(cls.root/'ap-data'),
                       PORT=str(cls.ap_port),HOST='127.0.0.1',PUBLIC_URL=cls.ap_url,NODE_ENV='production',
                       AGENTPULSE_AI_ENABLED='false',AGENTPULSE_TELEMETRY='off',DO_NOT_TRACK='1')
        cls.bun=ROOT/'.p0/tools/bun-darwin-aarch64/bun'
        cls.release=Path(os.environ.get('WORKCONTEXT_P1_RELEASE',str(ROOT/'.p1/releases/agentpulse-p1-v2'))).resolve()
        cls.ap=subprocess.Popen([str(cls.bun),'dist/server/index.js'],cwd=cls.release,env=cls.env,stdout=cls.ap_log,stderr=cls.ap_log)
        wait(lambda:request(cls.ap_url,'/api/v1/health')[0]==200)
        cls.admin=re.search(r'Created default API key: (ap_\S+)',(cls.root/'agentpulse.log').read_text())[1]
        cls.keys=[cls.admin]
        def mint(scopes):
            code,data=request(cls.ap_url,'/api/v1/api-keys',cls.admin,{'name':'p1-test','scopes':scopes});assert code==200,data
            cls.keys.append(data['key']);return data['key']
        cls.ingest=mint(['ingest']);cls.observe=mint(['observe'])
    @classmethod
    def tearDownClass(cls):
        cls.ap.terminate();cls.ap.wait(timeout=15);cls.ap_log.close()
        p=cls.root/'agentpulse.log';text=p.read_text()
        for key in cls.keys:text=text.replace(key,'[REDACTED]')
        p.write_text(text)
    def setUp(self):
        self.root=self.__class__.root/uuid.uuid4().hex;self.root.mkdir()
        self.children=[];self.handles=[]
        init(self.root/'server','server','', '')
        self.sc=self.root/'server/config.json';self.server=json.loads(self.sc.read_text());self.server['port']=port()
        self.server['agentpulse']={'url':self.ap_url,'ingest_token':self.ingest,'observe_token':self.observe}
        self.base='http://127.0.0.1:'+str(self.server['port'])
        self.read=json.loads((self.root/'server/read-token.json').read_text())['token']
        init(self.root/'collector','collector','macbook-test',self.base)
        self.cc=self.root/'collector/config.json';self.collector=json.loads(self.cc.read_text());self.collector['port']=port()
        self.local=json.loads((self.root/'collector/read-token.json').read_text())['token']
        self.cb='http://127.0.0.1:'+str(self.collector['port'])
        self.server['devices']={'macbook-test':{'device_id':'macbook-test','token_hash':digest(self.collector['uplink_token']),'grant':self.collector['grant']}}
        self.save();self.sp=self.start(self.sc,'server');self.cp=self.start(self.cc,'collector')
    def tearDown(self):
        for p in self.children:
            if p.poll() is None:p.terminate();p.wait(timeout=10)
        for f in self.handles:f.close()
    def save(self):
        self.sc.write_text(json.dumps(self.server));self.cc.write_text(json.dumps(self.collector))
    def start(self,config,name):
        f=open(self.root/(name+'.log'),'ab');self.handles.append(f)
        p=subprocess.Popen([sys.executable,'-m','workcontext.service','serve','--config',str(config)],cwd=ROOT,stdout=f,stderr=f)
        self.children.append(p)
        base=self.base if name=='server' else self.cb
        wait(lambda:request(base,'/health')[0]==200)
        return p
    def raw(self,kind='UserPromptSubmit'):
        return {'session_id':'p1-'+uuid.uuid4().hex,'source_event_id':str(uuid.uuid4()),'hook_event_name':kind,
                'cwd':self.collector['allowed_roots'][0],'occurred_at':now(),'classification':'synthetic','prompt':FIXTURE}
    def post(self,raw,agent='codex_cli'):
        return request(self.cb,'/api/v1/hooks',self.local,raw,headers={'X-Agent-Type':agent})
    def rows(self,path=None):
        with sqlite3.connect(path or self.server['database']) as db:return db.execute('SELECT event_id,envelope,state FROM events').fetchall()
    def test_01_packaged_dashboard(self):
        with urlopen(self.ap_url,timeout=3) as r:
            self.assertEqual(r.status,200);self.assertIn('<html',r.read().decode())
    def test_02_dedup_conflict_and_provider_namespaces(self):
        raw=self.raw();code,ack=self.post(raw);self.assertEqual(code,200)
        self.assertTrue(self.post(raw)[1]['duplicate'])
        altered=dict(raw,hook_event_name='Stop');self.assertEqual(self.post(altered)[0],409)
        self.assertEqual(self.post(raw,'claude_code')[0],200)
        wait(lambda:len(self.rows())==2)
        wait(lambda:all(r[2]=='delivered' for r in self.rows()))
        self.assertEqual(len(self.rows(self.collector['database'])),2)
    def test_03_device_spoof_and_untrusted_payload(self):
        event=adapt_hook(self.raw(),'codex_cli',self.collector)
        event['device_id']='windows-test'
        event['event_id']=digest(':'.join(event[x] for x in ('device_id','agent','session_id','source_event_id')))
        self.assertEqual(request(self.base,'/workcontext/v1/ingest',self.collector['uplink_token'],event)[0],403)
        for changes in [{'raw_payload':{'secret':'COMPANY-CANARY'}},{'prompt':'COMPANY-CANARY'},{'classification':'company-internal'},{'cwd':None},{'source_event_id':None}]:
            self.assertIn(self.post(dict(self.raw(),**changes))[0],[400,403])
        self.assertEqual(self.rows(self.collector['database']),[])
    def test_04_symlink_escape(self):
        forbidden=self.root/'forbidden';forbidden.mkdir()
        link=Path(self.collector['allowed_roots'][0])/'escape';link.symlink_to(forbidden,target_is_directory=True)
        self.assertEqual(self.post(dict(self.raw(),cwd=str(link)))[0],403)
        self.assertEqual(self.rows(),[])
    def test_05_auth_and_control_routes(self):
        self.assertEqual(request(self.base,'/workcontext/v1/events')[0],401)
        self.assertEqual(request(self.cb,'/api/v1/hooks',body=self.raw())[0],401)
        for path in ['/api/v1/launches','/api/v1/sessions/x/claude-md','/api/v1/supervisors/register']:
            self.assertEqual(request(self.base,path,self.collector['uplink_token'],{})[0],403)
        self.assertEqual(request(self.base,'/workcontext/v1/ingest',self.read,{})[0],401)
        self.assertEqual(self.post(dict(self.raw(),cwd=str(self.root)))[0],403)
        self.assertEqual(request(self.cb,'/api/v1/hooks',self.local,self.raw(),headers={'Origin':'https://evil.invalid'})[0],403)
    def test_06_offline_outbox_and_collector_crash(self):
        self.sp.kill();self.sp.wait()
        raw=self.raw();self.assertEqual(self.post(raw)[0],200)
        wait(lambda:len(self.rows(self.collector['database']))==1)
        self.cp.kill();self.cp.wait();self.cp=self.start(self.cc,'collector')
        self.sp=self.start(self.sc,'server')
        wait(lambda:len(self.rows())==1)
        wait(lambda:self.rows(self.collector['database'])[0][2]=='delivered')
        self.assertTrue(self.post(raw)[1]['duplicate'])
    def test_07_server_crash_durable_ack_and_replay(self):
        event=adapt_hook(self.raw(),'claude_code',self.collector)
        token=self.collector['uplink_token']
        self.assertTrue(request(self.base,'/workcontext/v1/ingest',token,event)[1]['durable'])
        self.sp.kill();self.sp.wait();self.sp=self.start(self.sc,'server')
        self.assertTrue(request(self.base,'/workcontext/v1/ingest',token,event)[1]['duplicate'])
        self.assertEqual(len(self.rows()),1)
    def test_08_revocation_while_offline(self):
        self.sp.kill();self.sp.wait()
        self.assertEqual(self.post(self.raw())[0],200)
        self.collector['grant']['enabled']=False;self.save()
        wait(lambda:self.rows(self.collector['database'])[0][2]=='revoked')
        self.sp=self.start(self.sc,'server');time.sleep(.5)
        self.assertEqual(self.rows(),[]);self.assertEqual(self.post(self.raw())[0],403)
    def test_09_central_revocation_and_expiry(self):
        self.server['devices']['macbook-test']['grant']=dict(self.collector['grant'],enabled=False);self.save()
        event=adapt_hook(self.raw(),'codex_cli',self.collector)
        self.assertEqual(request(self.base,'/workcontext/v1/ingest',self.collector['uplink_token'],event)[0],403)
        self.collector['grant']['expires_at']='2000-01-01T00:00:00Z';self.save()
        self.assertEqual(self.post(self.raw())[0],403)
    def test_10_queue_limit_and_no_silent_drop(self):
        self.sp.kill();self.sp.wait()
        self.collector['capacity']=1;self.save()
        self.assertEqual(self.post(self.raw())[0],200)
        self.assertEqual(self.post(self.raw())[0],503)
        self.assertEqual(len(self.rows(self.collector['database'])),1)
    def test_11_agentpulse_projection_retry_is_unique(self):
        event=adapt_hook(self.raw(),'claude_code',self.collector)
        token=self.collector['uplink_token']
        self.assertEqual(request(self.base,'/workcontext/v1/ingest',token,event)[0],200)
        wait(lambda:self.rows() and self.rows()[0][2]=='delivered')
        with sqlite3.connect(self.server['database']) as db:db.execute("UPDATE events SET state='pending',next_attempt=0")
        wait(lambda:self.rows()[0][2]=='delivered')
        from workcontext.service import project
        sid,_=project(event)
        code,data=request(self.ap_url,'/api/v1/sessions/'+sid+'/timeline',self.observe)
        self.assertEqual(code,200)
        matching=[x for x in data['events'] if x.get('rawPayload',{}).get('workcontext_event_id')==event['event_id']]
        self.assertEqual(len(matching),1)
    def test_12_backup_integrity(self):
        self.assertEqual(self.post(self.raw())[0],200);wait(lambda:len(self.rows())==1)
        backup=self.root/'backup.sqlite'
        with sqlite3.connect(self.server['database']) as source,sqlite3.connect(backup) as target:
            source.backup(target)
            self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(target.execute('SELECT count(*) FROM events').fetchone()[0],1)
    def test_13_instruction_files_never_modified(self):
        root=Path(self.collector['allowed_roots'][0])
        sentinels={root/'CLAUDE.md':'DO NOT CHANGE CLAUDE',root/'AGENTS.md':'DO NOT CHANGE AGENTS'}
        for path,text in sentinels.items():path.write_text(text)
        self.assertEqual(self.post(self.raw())[0],200)
        wait(lambda:len(self.rows())==1)
        wait(lambda:self.rows()[0][2]=='delivered')
        for path,text in sentinels.items():self.assertEqual(path.read_text(),text)
        self.assertEqual({p.name for p in root.iterdir()},{'CLAUDE.md','AGENTS.md'})
    def test_14_enrollment_has_no_bearer_and_rejects_path_injection(self):
        other=self.root/'logical-second';init(other,'collector','logical-second',self.base)
        output=self.root/'enrollment.json'
        export=[sys.executable,str(ROOT/'deploy/p1/enrollment.py'),'export','--collector-config',str(other/'config.json'),'--output',str(output)]
        subprocess.run(export,check=True,capture_output=True)
        device=json.loads(output.read_text());secret=json.loads((other/'config.json').read_text())['uplink_token']
        self.assertNotIn(secret,output.read_text())
        command=[sys.executable,str(ROOT/'deploy/p1/enrollment.py'),'import','--server-config',str(self.sc),'--enrollment',str(output)]
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,1)
        device['device_id']='../../escape';output.write_text(json.dumps(device))
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,1)
    def test_15_plain_remote_http_rejected_before_network(self):
        from workcontext.service import http
        with self.assertRaisesRegex(ValueError,'https_required'):
            http('http://example.invalid/workcontext/v1/ingest','DO-NOT-SEND',{})
    def test_16_fixture_cli_retry_and_new_run_are_distinct(self):
        output=self.root/'fixture-acks.json'
        command=[sys.executable,str(ROOT/'scripts/p1-fixture.py'),'--config',str(self.cc),
                 '--token-file',str(self.cc.parent/'read-token.json'),'--output',str(output),'--run-id']
        runid=uuid.uuid4().hex[:16]
        subprocess.run(command+[runid],check=True,capture_output=True,timeout=10)
        first=json.loads(output.read_text());self.assertEqual(len(first),12)
        ids={x['ack']['event_id'] for x in first}
        subprocess.run(command+[runid],check=True,capture_output=True,timeout=10)
        self.assertTrue(all(x['ack']['duplicate'] for x in json.loads(output.read_text())))
        subprocess.run(command+[uuid.uuid4().hex[:16]],check=True,capture_output=True,timeout=10)
        self.assertTrue(ids.isdisjoint({x['ack']['event_id'] for x in json.loads(output.read_text())}))
        wait(lambda:len(self.rows())==24)


if __name__=='__main__':unittest.main(verbosity=2)
