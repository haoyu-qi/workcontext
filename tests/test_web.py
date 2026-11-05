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
