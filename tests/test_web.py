"""Browser facade against real P2 HTTP and SQLite authorization, no mocks."""
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_p2
from test_p1 import port,wait
ROOT=Path(__file__).resolve().parents[1]
class WebConsole(unittest.TestCase):
    def test_local_sources_are_read_only_and_scoped(self):
        import sqlite3
        from workcontext.web_sources import LocalSources
        from workcontext.service import Rejected
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=root/'outbox.sqlite';credential=root/'token.json'
            credential.write_text(json.dumps({'token':'local-reader','expires_at':'2099-01-01T00:00:00Z'}))
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE outbox (id TEXT PRIMARY KEY,body TEXT,state TEXT)')
                for identity,state in [('pending','pending'),('delivered','delivered'),('blocked','blocked')]:
                    body={'id':identity,'text':'source search text','project':'workcontext','agent':'codex','device':'macbook','metadata':{'source_path':'/original/log'}}
                    db.execute('INSERT INTO outbox VALUES (?,?,?)',(identity,json.dumps(body),state))
            config={'database':str(database),'token_file':str(credential)}
            with self.assertRaises(Rejected):LocalSources(config,'wrong')
            sources=LocalSources(config,'local-reader')
            self.assertEqual(sources.stats()['records'],2)
            self.assertEqual(sources.stats()['queue'],{'pending':1,'delivered':1})
            self.assertEqual(len(sources.search('workcontext')['matches']),2)
            self.assertEqual(sources.search("'; DROP TABLE outbox; --")['matches'],[])
            self.assertEqual(sources.get('pending')['delivery_state'],'pending')
            with self.assertRaises(Rejected):sources.get('blocked')
            with sources.db() as db:
                with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM outbox')

    def test_real_memory_facade(self):
        from workcontext.long_memory import Store
        from workcontext.service import digest
        from urllib.parse import urlencode
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);children=[]
            token='SYNTHETIC-memory-reader';database=root/'memory.sqlite'
            store=Store(database)
            record={'id':'a'*64,'device':'macbook','agent':'codex','session':'session','project':'workcontext','ordinal':'1','text':'Searchable source with <script>markup</script>','occurred':'2026-11-05','metadata':{'path':'/local/source.jsonl'}}
            record['text']+=' '+chr(0x4e2d)*11900
            store.ingest([record,dict(record,id='b'*64)]);note=store.remember('Searchable stable note',title='Source-backed note',evidence=[record['id']])
            memory=root/'memory.json';policy={'role':'reader','enabled':True,'expires_at':'2099-01-01T00:00:00Z','token_hash':digest(token)}
            memory.write_text(json.dumps({'bind':'127.0.0.1','port':43170,'database':str(database),'clients':{'test':policy}}))
            webport=port();base='http://127.0.0.1:'+str(webport)
            credential_file=root/'reader.json';credential_file.write_text(json.dumps({'token':token}))
            config=root/'web.json';config.write_text(json.dumps({'port':webport,'memory_url':'http://127.0.0.1:43170','origins':[base],'auth':{'database':str(root/'sessions.sqlite'),'credentials':{'memory':str(credential_file)}}}))
            def req(path,credential=token,body=None,headers=None):
                h={'Authorization':'Bearer '+credential,**(headers or {})}
                try:
                    with urlopen(Request(base+path,headers=h,data=json.dumps(body).encode() if body is not None else None),timeout=5) as response:return response.status,json.loads(response.read())
                except HTTPError as error:return error.code,json.loads(error.read())
            try:
                children.append(subprocess.Popen([sys.executable,'-m','workcontext.memory_service','--config',str(memory)],cwd=ROOT))
                children.append(subprocess.Popen([sys.executable,'-m','workcontext.web_service','--config',str(config)],cwd=ROOT))
                wait(lambda:req('/health')[0]==200)
                self.assertEqual(req('/health')[1]['services'],{'memory':{'available':True}})
                self.assertEqual(req('/api/memory-stats')[1]['records'],2)
                self.assertEqual(req('/api/memory-stats','invalid')[0],401)
                self.assertEqual(len(req('/api/memory-search?'+urlencode({'q':'Searchable'}))[1]['matches']),3)
                self.assertEqual(req('/api/memory-get?id='+note['id'])[1]['evidence'],json.dumps([record['id']]))
                self.assertEqual(req('/api/memory-get?id='+record['id'])[1]['metadata'],json.dumps(record['metadata'],sort_keys=True))
                for path in ['/api/memory-search?q=','/api/memory-search?q=a&q=b','/api/memory-stats?extra=1','/api/memory-get']:
                    self.assertEqual(req(path)[0],400)
                self.assertEqual(req('/api/memory-stats',headers={'Origin':'https://bad.invalid'})[0],403)
                self.assertEqual(req('/api/memory-remember',body={'content':'forbidden'})[0],403)
                from workcontext.web_auth import Sessions
                def session_req(path,cookie='',body=None,origin=None):
                    headers={'Cookie':cookie}
                    if origin:headers['Origin']=origin
                    try:
                        with urlopen(Request(base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=5) as response:return response.status,json.load(response),response.headers
                    except HTTPError as error:return error.code,json.load(error),error.headers
                sessions=Sessions(config);code=sessions.create_pair()['code']
                self.assertEqual(session_req('/auth/pair',body={'code':code})[0],403)
                status,data,headers=session_req('/auth/pair',body={'code':code},origin=base)
                self.assertEqual(status,200);self.assertNotIn('token',data)
                cookie=headers['Set-Cookie'].split(';')[0]
                self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertNotIn('Secure',headers['Set-Cookie'])
                self.assertEqual(session_req('/auth/pair',body={'code':code},origin=base)[0],401)
                self.assertEqual(session_req('/auth/session',cookie)[1]['services'],['memory'])
                self.assertEqual(session_req('/api/memory-stats',cookie)[1]['records'],2)
                self.assertEqual(session_req('/api/inventory',cookie)[0],403)
                self.assertEqual(session_req('/api/handoff',cookie,body={})[0],403)
                self.assertEqual(session_req('/auth/logout',cookie,body={},origin='https://bad.invalid')[0],403)
                self.assertEqual(session_req('/auth/logout',cookie,body={},origin=base)[0],200)
                self.assertEqual(session_req('/api/memory-stats',cookie)[0],401)
                # Policy revocation is still checked by the upstream for cookie sessions.
                code=sessions.create_pair()['code']
                cookie=session_req('/auth/pair',body={'code':code},origin=base)[2]['Set-Cookie'].split(';')[0]
                policy['enabled']=False;memory.write_text(json.dumps({'bind':'127.0.0.1','port':43170,'database':str(database),'clients':{'test':policy}}))
                self.assertEqual(req('/api/memory-stats')[0],401)
                self.assertEqual(session_req('/api/memory-stats',cookie)[0],401)
            finally:
                for child in reversed(children):child.terminate();child.wait(timeout=10)

    def test_console_policy_and_origin(self):
        fixture=test_p2.P2Engine('runTest');fixture.setUp();children=[]
        try:
            fixture.c['port']=43140;fixture.save()
            p=subprocess.Popen([sys.executable,'-m','workcontext.context_service','serve','--config',str(fixture.config)],cwd=ROOT);children.append(p)
            webport=port();base='http://127.0.0.1:'+str(webport)
            inventory=fixture.root/'inventory.json';inventory.write_text(json.dumps({'devices':[{'name':'SYNTHETIC fixture'}]}))
            cfg=fixture.root/'web.json';cfg.write_text(json.dumps({'port':webport,'upstream':'http://127.0.0.1:43140','origins':[base],'inventory_file':str(inventory)}))
            web=subprocess.Popen([sys.executable,'-m','workcontext.web_service','--config',str(cfg)],cwd=ROOT);children.append(web)
            def req(path,token=None,body=None,headers=None):
                h=dict(headers or {})
                if token:h['Authorization']='Bearer '+token
                r=Request(base+path,headers=h,data=json.dumps(body).encode() if body is not None else None)
                try:
                    with urlopen(r,timeout=3) as response:return response.status,response.read(),response.headers
                except HTTPError as e:return e.code,e.read(),e.headers
            wait(lambda:req('/health')[0]==200)
            status,raw,h=req('/');self.assertEqual(status,200);self.assertIn(b'WorkContext',raw);self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
            for path in ['/app.js','/token.mjs','/style.css']:self.assertEqual(req(path)[0],200)
            self.assertEqual(req('/api/inventory')[0],401)
            self.assertEqual(req('/api/inventory','invalid')[0],401)
            self.assertEqual(req('/api/inventory',fixture.token)[0],200)
            status,raw,_=req('/api/task?project_id=p2-demo&task_id=shared-task',fixture.token)
            self.assertEqual(status,200);self.assertEqual(json.loads(raw)['current_state']['state'],'pending_validation')
            self.assertNotIn(b'PRIVATE-P2-SENTINEL',raw)
            self.assertEqual(req('/api/project?project_id=p2-private',fixture.token)[0],404)
            self.assertEqual(req('/api/activity',fixture.token,headers={'Origin':'https://bad.invalid'})[0],403)
            self.assertEqual(req('/api/activity',fixture.token,headers={'Host':'bad.invalid'})[0],403)
            self.assertEqual(req('/api/activity',fixture.token,headers={'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(req('/api/activity?project_id=a&project_id=b',fixture.token)[0],400)
            self.assertEqual(req('/api/execute',fixture.token,{'command':'bad'})[0],403)
            body={'project_id':'p2-demo','task_id':'shared-task','target_agent':'codex_cli','target_device':'macbook','token_budget':12000}
            status,raw,_=req('/api/handoff',fixture.token,body,{'Origin':base});self.assertEqual(status,200);self.assertTrue(json.loads(raw)['evidence'])
            body['target_agent']='hermes';self.assertEqual(req('/api/handoff',fixture.token,body,{'Origin':base})[0],403)
            fixture.c['readers']['test']['enabled']=False;fixture.save()
            self.assertEqual(req('/api/inventory',fixture.token)[0],403)
        finally:
            for child in reversed(children):child.terminate();child.wait(timeout=10)
            fixture.tearDown()
if __name__=='__main__':unittest.main(verbosity=2)
