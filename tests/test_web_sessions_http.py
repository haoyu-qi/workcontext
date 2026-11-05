"""Portable HTTP session checks, independent of historical AgentPulse fixtures."""
import json,tempfile,threading,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from workcontext.web_service import Handler
from workcontext.memory_service import Handler as MemoryHandler
from workcontext.web_auth import Sessions
from workcontext.service import digest

class SessionHTTP(unittest.TestCase):
    def test_pair_refresh_logout_and_upstream_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);token='SYNTHETIC-reader-secret'
            credential=root/'reader.json';credential.write_text(json.dumps({'token':token}))
            memory_config=root/'memory.json'
            policy={'role':'reader','enabled':True,'expires_at':'2099-01-01T00:00:00Z','token_hash':digest(token)}
            memory_settings={'database':str(root/'memory.sqlite'),'clients':{'reader':policy}}
            memory_config.write_text(json.dumps(memory_settings))
            memory=ThreadingHTTPServer(('127.0.0.1',0),MemoryHandler);memory.config=memory_config
            web=ThreadingHTTPServer(('127.0.0.1',0),Handler);web.config=root/'web.json'
            base='http://127.0.0.1:'+str(web.server_port)
            config={'memory_url':'http://127.0.0.1:'+str(memory.server_port),'origins':[base,'https://console.example'],'auth':{'database':str(root/'sessions.sqlite'),'credentials':{'memory':str(credential)}}}
            web.config.write_text(json.dumps(config))
            for server in [memory,web]:threading.Thread(target=server.serve_forever,daemon=True).start()
            def request(path,cookie='',body=None,origin=None,host=None):
                headers={'Cookie':cookie}
                if origin:headers['Origin']=origin
                if host:headers['Host']=host
                try:
                    with urlopen(Request(base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None),timeout=5) as r:return r.status,json.load(r),r.headers
                except HTTPError as e:return e.code,json.load(e),e.headers
            try:
                code=Sessions(config).create_pair()['code']
                self.assertEqual(request('/auth/pair',body={'code':code})[0],403)
                self.assertEqual(request('/auth/pair',body={'code':code},origin='https://console.example')[0],403)
                status,data,headers=request('/auth/pair',body={'code':code},origin='https://console.example',host='console.example')
                self.assertEqual(status,200);self.assertNotIn(token,json.dumps(data))
                cookie=headers['Set-Cookie'].split(';')[0]
                self.assertIn('Secure',headers['Set-Cookie']);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
                self.assertEqual(request('/auth/pair',body={'code':code},origin=base)[0],401)
                self.assertEqual(request('/auth/session',cookie)[1]['services'],['memory'])
                self.assertEqual(request('/api/memory-stats',cookie)[1]['records'],0)
                self.assertEqual(request('/api/inventory',cookie)[0],403)
                self.assertEqual(request('/api/handoff',cookie,body={})[0],403)
                self.assertEqual(request('/api/memory-remember',cookie,body={},origin=base)[0],403)
                policy['enabled']=False;memory_config.write_text(json.dumps(memory_settings))
                self.assertEqual(request('/api/memory-stats',cookie)[0],401)
                self.assertEqual(request('/auth/logout',cookie,body={},origin=base)[0],200)
                self.assertEqual(request('/auth/session',cookie)[0],401)
                self.assertEqual(request('/api/memory-stats',cookie)[0],401)
            finally:
                for server in [web,memory]:server.shutdown();server.server_close()
