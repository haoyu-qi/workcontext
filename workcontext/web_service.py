"""Private, same-origin browser facade over the existing P2 permission boundary."""
import argparse,json,signal,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,urlencode
from urllib.error import HTTPError
from .service import http,load

ASSETS=Path(__file__).with_name('web')
ROUTES={'activity':set(['project_id','task_id']),'search':{'q'},'project':{'project_id'},'task':{'project_id','task_id'}}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value,mime='application/json'):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_PUT(self):self.reply(403,{'error':'read_only'})
    do_DELETE=do_PUT
    do_PATCH=do_PUT
    def dispatch(self):
        self.connection.settimeout(10)
        try:
            c=load(self.server.config);u=urlsplit(self.path)
            allowed=c['origins'];host=self.headers.get('Host','')
            if host not in [urlsplit(x).netloc for x in allowed]:return self.reply(403,{'error':'host_denied'})
            origin=self.headers.get('Origin')
            if origin and origin not in allowed:return self.reply(403,{'error':'origin_denied'})
            if self.headers.get('Sec-Fetch-Site')=='cross-site':return self.reply(403,{'error':'origin_denied'})
            if self.command=='GET' and u.path in ['/', '/app.js','/token.mjs','/style.css']:
                name={'/':'index.html','/app.js':'app.js','/token.mjs':'token.mjs','/style.css':'style.css'}[u.path]
                return self.reply(200,(ASSETS/name).read_bytes(),{'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','token.mjs':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name])
            if self.command=='GET' and u.path=='/health':
                try:up=http(c['upstream']+'/health','');healthy=True
                except Exception:up={'error':'upstream_unavailable'};healthy=False
                return self.reply(200 if healthy else 503,{'service':'workcontext-web','upstream':up})
            auth=self.headers.get('Authorization','')
            if not auth.startswith('Bearer ') or len(auth)>4096:return self.reply(401,{'error':'unauthorized'})
            token=auth[7:]
            if self.command=='GET' and u.path=='/api/inventory' and not u.query:
                # Use the upstream policy check, including enabled/expiry, before returning inventory.
                http(c['upstream']+'/workcontext/v2/activity',token)
                return self.reply(200,load(c['inventory_file']))
            if self.command=='GET' and u.path in ['/api/execution','/api/execution-job']:
                if c.get('execution_url')!='http://127.0.0.1:43160':return self.reply(503,{'error':'execution_unavailable'})
                q=parse_qs(u.query,keep_blank_values=True)
                if u.path=='/api/execution':
                    if q:return self.reply(400,{'error':'invalid_query'})
                    route='/state'
                else:
                    if set(q)!={'id'} or len(q['id'])!=1:return self.reply(400,{'error':'invalid_query'})
                    route='/job?'+urlencode({'id':q['id'][0]})
                return self.reply(200,http(c['execution_url']+route,token))
            name=u.path.removeprefix('/api/');q=parse_qs(u.query,keep_blank_values=True)
            if self.command=='GET' and u.path.startswith('/api/') and name in ROUTES:
                if any(len(v)!=1 for v in q.values()) or set(q)-ROUTES[name]:return self.reply(400,{'error':'invalid_query'})
                return self.reply(200,http(c['upstream']+'/workcontext/v2/'+name+('?' +urlencode({k:v[0] for k,v in q.items()}) if q else ''),token))
            if self.command=='POST' and u.path=='/api/handoff' and not u.query:
                if self.headers.get('Transfer-Encoding'):return self.reply(400,{'error':'invalid_body'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:return self.reply(413,{'error':'body_size'})
                body=json.loads(self.rfile.read(length))
                return self.reply(200,http(c['upstream']+'/workcontext/v2/handoff',token,body))
            self.reply(403,{'error':'route_denied'})
        except HTTPError as e:
            try:value=json.loads(e.read(4096))
            except Exception:value={'error':'upstream_refused'}
            self.reply(e.code,value)
        except (ValueError,UnicodeError):self.reply(400,{'error':'invalid_request'})
        except Exception:self.reply(503,{'error':'service_unavailable'})

def serve(config):
    c=load(config);u=urlsplit(c['upstream'])
    if u.scheme!='http' or u.hostname!='127.0.0.1' or u.port!=43140 or u.path not in ('','/'):raise ValueError('fixed_local_p2_required')
    s=ThreadingHTTPServer(('127.0.0.1',c['port']),Handler);s.daemon_threads=True;s.config=config
    def stop(*_):threading.Thread(target=s.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:s.serve_forever(.2)
    finally:s.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);serve(p.parse_args().config)
