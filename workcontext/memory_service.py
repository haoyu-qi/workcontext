"""Authenticated append-only source gateway and separate owner memory tools."""
import argparse,json,signal,threading
from urllib.parse import urlsplit,parse_qs
from http.server import BaseHTTPRequestHandler
from .service import load,token_ok,active,Rejected,canonical
from .execution import LoopbackServer
from .long_memory import Store
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def reply(self,status,value):
        raw=canonical(value).encode();self.send_response(status)
        for k,v in {'Content-Type':'application/json','Content-Length':str(len(raw)),'Cache-Control':'no-store'}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_DELETE(self):self.reply(403,{'error':'route_denied'})
    do_PUT=do_DELETE
    do_PATCH=do_DELETE
    def dispatch(self):
        self.connection.settimeout(15)
        try:
            c=load(self.server.config);u=urlsplit(self.path)
            if self.command=='GET' and u.path=='/health':return self.reply(200,{'service':'workcontext-memory','persistent':True})
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):raise Rejected('origin_or_encoding_denied')
            token=self.headers.get('Authorization','').removeprefix('Bearer ');policy=next((x for x in c['clients'].values() if token_ok(token,x.get('token_hash')) and active(x)),None)
            if not policy:raise Rejected('unauthorized',401)
            store=Store(c['database']);q=parse_qs(u.query,keep_blank_values=True)
            if any(len(v)!=1 for v in q.values()):raise Rejected('invalid_query',400)
            if self.command=='POST':
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=1048576:raise Rejected('body_size',413)
                body=json.loads(self.rfile.read(n))
                if u.path=='/sources/ingest' and not q and policy['role']=='ingest':
                    if not isinstance(body,dict) or set(body)!={'records'}:raise Rejected('invalid_fields',400)
                    if not isinstance(body['records'],list):raise Rejected('invalid_records',400)
                    for row in body['records']:
                        if not isinstance(row,dict) or row.get('agent') not in policy['agents']:raise Rejected('agent_denied')
                        cwd=(row.get('metadata') or {}).get('cwd','')
                        if policy.get('roots') and not any(cwd==root or cwd.startswith(root.rstrip('/')+'/') for root in policy['roots']):raise Rejected('source_root_denied')
                    result=store.ingest(body['records'],policy['device'])
                elif u.path=='/memory/remember' and not q and policy['role']=='owner':
                    if not isinstance(body,dict) or not {'content'}<=set(body) or set(body)-{'content','title','kind','project','evidence','note_id'}:raise Rejected('invalid_fields',400)
                    result=store.remember(**body)
                else:raise Rejected('route_denied')
            elif policy['role'] in ['owner','reader']:
                if u.path=='/memory/search' and set(q)=={'q'}:result=store.search(q['q'][0])
                elif u.path=='/memory/context' and set(q)=={'q'}:result=store.context(q['q'][0])
                elif u.path=='/memory/get' and set(q)=={'id'}:result=store.get(q['id'][0])
                elif u.path=='/stats' and not q:result=store.stats()
                else:raise Rejected('route_denied')
            else:raise Rejected('route_denied')
            self.reply(200,result)
        except Rejected as e:self.reply(e.status,{'error':e.reason})
        except (ValueError,TypeError,KeyError):self.reply(400,{'error':'invalid_request'})
        except Exception:self.reply(503,{'error':'memory_unavailable'})
def serve(config):
    c=load(config)
    if c['bind']!='127.0.0.1':raise ValueError('loopback_only')
    Store(c['database']).stats();server=LoopbackServer((c['bind'],c['port']),Handler);server.config=config;server.daemon_threads=True
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever(.2)
    finally:server.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);serve(p.parse_args().config)
