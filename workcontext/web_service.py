"""Private, same-origin browser facade over the existing P2 permission boundary."""
import argparse,json,signal,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,urlencode
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler
from .service import http,load,NoRedirect,Rejected
from .web_sources import LocalSources
from .web_auth import Sessions,session_cookie

ASSETS=Path(__file__).with_name('web')
MEMORY_ROUTES={'memory-stats':('/stats',set()),'memory-search':('/memory/search',{'q'}),'memory-get':('/memory/get',{'id'})}
ROUTES={'activity':set(['project_id','task_id']),'search':{'q'},'project':{'project_id'},'task':{'project_id','task_id'}}
def memory_http(config,path,token):
    bases=list(dict.fromkeys([config['memory_url']]+config.get('memory_fallback_urls',[])))
    for index,base in enumerate(bases):
        try:
            request=Request(base.rstrip('/')+path,headers={'Authorization':'Bearer '+token})
            with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=10) as response:
                return json.loads(response.read(2097152))
        except HTTPError:raise
        except OSError:
            if index==len(bases)-1:raise

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value,mime='application/json',headers=None):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():self.send_header(k,v)
        for k,v in (headers or {}).items():self.send_header(k,v)
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
            if self.command=='GET' and u.path in ['/', '/app.js','/memory.js','/credentials-ui.js','/session-ui.js','/token.mjs','/style.css']:
                name={'/':'index.html','/app.js':'app.js','/memory.js':'memory.js','/credentials-ui.js':'credentials-ui.js','/session-ui.js':'session-ui.js','/token.mjs':'token.mjs','/style.css':'style.css'}[u.path]
                return self.reply(200,(ASSETS/name).read_bytes(),{'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','memory.js':'text/javascript; charset=utf-8','credentials-ui.js':'text/javascript; charset=utf-8','session-ui.js':'text/javascript; charset=utf-8','token.mjs':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name])
            if self.command=='GET' and u.path=='/config' and not u.query:
                return self.reply(200,{'context':bool(c.get('upstream')),'memory':bool(c.get('memory_url')),'execution':bool(c.get('execution_url')),'local':bool(c.get('local_sources')),'pairing':bool(c.get('auth'))})
            if self.command=='GET' and u.path=='/health':
                services={}
                if c.get('local_sources'):
                    services['local']={'available':Path(c['local_sources']['database']).expanduser().is_file()}
                for name,key in [('context','upstream'),('memory','memory_url'),('execution','execution_url')]:
                    if not c.get(key):continue
                    try:
                        if name=='memory':memory_http(c,'/health','')
                        else:http(c[key].rstrip('/')+'/health','')
                        services[name]={'available':True}
                    except Exception:services[name]={'available':False}
                healthy=any(v['available'] for v in services.values())
                return self.reply(200 if healthy else 503,{'service':'workcontext-web','services':services})
            if u.path.startswith('/auth/'):
                sessions=Sessions(c);cookie=self.headers.get('Cookie','')
                secure=any(urlsplit(x).scheme=='https' and urlsplit(x).netloc==host for x in allowed)
                if self.command=='GET' and u.path=='/auth/session' and not u.query:
                    value=sessions.resolve(cookie)
                    return self.reply(200,{'authenticated':True,'services':list(value['credentials']),'expires_at':value['expires_at']})
                if self.command=='POST' and not u.query and u.path in ['/auth/pair','/auth/logout']:
                    if not origin or urlsplit(origin).netloc!=host:return self.reply(403,{'error':'origin_denied'})
                    if u.path=='/auth/logout':
                        sessions.logout(cookie)
                        return self.reply(200,{'authenticated':False},headers={'Set-Cookie':session_cookie('',0,secure)})
                    if self.headers.get('Transfer-Encoding'):return self.reply(400,{'error':'invalid_body'})
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=1024:return self.reply(413,{'error':'body_size'})
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict) or set(body)!={'code'}:return self.reply(400,{'error':'invalid_body'})
                    value=sessions.redeem(body['code'])
                    return self.reply(200,{'authenticated':True,'services':value['services'],'expires_at':value['expires_at']},headers={'Set-Cookie':session_cookie(value['token'],value['seconds'],secure)})
                return self.reply(403,{'error':'route_denied'})
            auth=self.headers.get('Authorization','')
            if auth:
                if not auth.startswith('Bearer ') or len(auth)>4096:return self.reply(401,{'error':'unauthorized'})
                token=auth[7:]
            elif c.get('auth'):
                if self.command=='POST' and (not origin or urlsplit(origin).netloc!=host):return self.reply(403,{'error':'origin_denied'})
                value=Sessions(c).resolve(self.headers.get('Cookie',''))
                service='memory' if u.path.startswith(('/api/memory-','/api/source-')) else 'context'
                token=value['credentials'].get(service)
                if not token:return self.reply(403,{'error':'session_scope_denied'})
            else:return self.reply(401,{'error':'unauthorized'})
            if self.command=='GET' and u.path in ['/api/source-stats','/api/source-search','/api/source-get']:
                if not c.get('local_sources'):return self.reply(503,{'error':'local_not_configured'})
                name=u.path.removeprefix('/api/source-');fields={'stats':set(),'search':{'q'},'get':{'id'}}[name]
                q=parse_qs(u.query,keep_blank_values=True)
                if set(q)!=fields or any(len(v)!=1 or not v[0].strip() for v in q.values()):return self.reply(400,{'error':'invalid_query'})
                sources=LocalSources(c['local_sources'],token)
                return self.reply(200,getattr(sources,name)(**{('query' if k=='q' else 'identity'):v[0] for k,v in q.items()}))
            memory_route=MEMORY_ROUTES.get(u.path.removeprefix('/api/')) if u.path.startswith('/api/') else None
            if self.command=='GET' and memory_route:
                if not c.get('memory_url'):return self.reply(503,{'error':'memory_not_configured'})
                route,fields=memory_route;q=parse_qs(u.query,keep_blank_values=True)
                if set(q)!=fields or any(len(v)!=1 or not v[0].strip() for v in q.values()):return self.reply(400,{'error':'invalid_query'})
                return self.reply(200,memory_http(c,route+('?' +urlencode({k:v[0] for k,v in q.items()}) if q else ''),token))
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
        except Rejected as e:self.reply(e.status,{'error':e.reason})
        except HTTPError as e:
            try:value=json.loads(e.read(4096))
            except Exception:value={'error':'upstream_refused'}
            self.reply(e.code,value)
        except (ValueError,UnicodeError):self.reply(400,{'error':'invalid_request'})
        except Exception:self.reply(503,{'error':'service_unavailable'})

def serve(config):
    c=load(config)
    if not c.get('upstream') and not c.get('memory_url') and not c.get('local_sources'):raise ValueError('upstream_required')
    for key,ports in [('upstream',{43140}),('execution_url',{43160})]:
        if not c.get(key):continue
        u=urlsplit(c[key])
        if u.scheme!='http' or u.hostname!='127.0.0.1' or u.port not in ports or u.path not in ('','/') or u.username or u.password or u.query or u.fragment:raise ValueError('fixed_local_service_required')
    destinations=([c['memory_url']]+c.get('memory_fallback_urls',[])) if c.get('memory_url') else []
    if len(destinations)>3:raise ValueError('too_many_destinations')
    for destination in destinations:
        u=urlsplit(destination)
        if not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment or not (u.scheme=='https' or (u.scheme=='http' and u.hostname=='127.0.0.1' and u.port in {43170,43172})):raise ValueError('unsafe_memory_destination')
    s=ThreadingHTTPServer(('127.0.0.1',c['port']),Handler);s.daemon_threads=True;s.config=config
    def stop(*_):threading.Thread(target=s.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:s.serve_forever(.2)
    finally:s.server_close()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);serve(p.parse_args().config)
