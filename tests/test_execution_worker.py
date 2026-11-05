"""Real scheduler HTTP and worker subprocess, durable replay and local registry guard."""
import hashlib,json,os,secrets,subprocess,sys,tempfile,time,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workcontext.execution_worker import revision,step,host_os
from workcontext.service import http,digest,canonical
from test_p1 import port,wait
ROOT=Path(__file__).resolve().parents[1]
class Worker(unittest.TestCase):
    def test_subprocess_execution_and_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workspace=root/'project';workspace.mkdir();script=workspace/'check.py';counter=workspace/'counter'
            script.write_text("from pathlib import Path\np=Path('counter'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('real subprocess evidence')\n")
            manifest=workspace/'manifest.json';manifest.write_text(json.dumps({'files':{'check.py':hashlib.sha256(script.read_bytes()).hexdigest()}}))
            project={'root':str(workspace),'manifest':str(manifest)};rev=revision(project);expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();node_token=secrets.token_urlsafe();op=secrets.token_urlsafe();server_port=port()
            policy={'enabled':True,'expires_at':expiry,'projects':['demo'],'capabilities':['check']}
            c={'database':str(root/'jobs.sqlite'),'bind':'127.0.0.1','port':server_port,'nodes':{'test':{**policy,'os':host_os(),'token_hash':digest(node_token)}},'operators':{'op':{**policy,'token_hash':digest(op)}},'readers':{}}
            cfg=root/'server.json';cfg.write_text(json.dumps(c));token=root/'token.json';token.write_text(json.dumps({'token':node_token}));base='http://127.0.0.1:'+str(server_port)
            worker={'node':'test','url':base,'token_file':str(token),'journal':str(root/'journal.sqlite'),'projects':{'demo':project},'capabilities':{'check':{'project':'demo','argv':[sys.executable,str(script)],'timeout':5}}};wc=root/'worker.json';wc.write_text(json.dumps(worker))
            log=(root/'server.log').open('w+b')
            child=subprocess.Popen([sys.executable,'-m','workcontext.execution','--config',str(cfg)],cwd=ROOT,stdout=log,stderr=log)
            try:
                last={}
                def ready():
                    try:
                        value=http(base+'/health','');last['response']=value;return value.get('service')=='workcontext-execution'
                    except Exception as error:last['error']=repr(error);return False
                try:wait(ready)
                except AssertionError:
                    log.flush();log.seek(0);self.fail('Server readiness failed: '+repr(last)+'; exit='+repr(child.poll())+'; log='+log.read().decode('utf-8','replace'))
                body={'project':'demo','revision':rev,'capability':'check','required_os':'any','idempotency_key':'once'}
                job=http(base+'/submit',op,body);self.assertTrue(step(wc));value=http(base+'/job?id='+job['id'],op)
                self.assertEqual(value['state'],'succeeded');self.assertIn('real subprocess',value['result']['stdout']);self.assertEqual(counter.read_text(),'1')
                self.assertFalse(step(wc));http(base+'/submit',op,body);self.assertFalse(step(wc));self.assertEqual(counter.read_text(),'1')
                # Changing the immutable source prevents another task from starting.
                script.write_text('raise RuntimeError("changed")')
                second=http(base+'/submit',op,{**body,'idempotency_key':'changed'})
                self.assertFalse(step(wc));self.assertEqual(http(base+'/job?id='+second['id'],op)['state'],'queued')
                # A timed-out process is actually killed and its result durably uploaded.
                script.write_text('import time\ntime.sleep(30)\n');manifest.write_text(json.dumps({'files':{'check.py':hashlib.sha256(script.read_bytes()).hexdigest()}}));worker['capabilities']['check']['timeout']=.2;wc.write_text(json.dumps(worker))
                j=http(base+'/submit',op,{**body,'revision':revision(project),'idempotency_key':'timeout'})
                self.assertTrue(step(wc));self.assertEqual(http(base+'/job?id='+j['id'],op)['state'],'timed_out')
            finally:child.terminate();child.wait(timeout=10);log.close()
if __name__=='__main__':unittest.main(verbosity=2)
