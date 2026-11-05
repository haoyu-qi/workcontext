"""P2 query-only API. Separate bind/credentials/DB from the P1 receiver."""
import argparse
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from .service import load,canonical,token_ok,Rejected
from .context_engine import Engine,migrate

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,data):
        raw=canonical(data).encode();self.send_response(code);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def do_PUT(self):self.reply(403,{'error':'read_only'})
    def do_DELETE(self):self.reply(403,{'error':'read_only'})
    def do_PATCH(self):self.reply(403,{'error':'read_only'})
    def dispatch(self):
        self.connection.settimeout(10)
        try:
            c=load(self.server.config);u=urlsplit(self.path)
            if self.command=='GET' and u.path=='/health':return self.reply(200,{'service':'workcontext-p2','read_only':True,'synthetic_only':True})
            if self.headers.get('Origin'):raise Rejected('origin_denied')
            token=self.headers.get('Authorization','').removeprefix('Bearer ')
            policy=next((p for p in c.get('readers',{}).values() if token_ok(token,p.get('token_hash'))),None)
            if not policy:raise Rejected('unauthorized',401)
            q=parse_qs(u.query,keep_blank_values=True)
            if any(len(v)!=1 for v in q.values()):raise Rejected('duplicate_query',400)
            args={k:v[0] for k,v in q.items()};engine=Engine(self.server.config)
            if self.command=='GET':
                if u.path=='/workcontext/v2/search' and set(args)=={'q'}:result=engine.search(policy,args['q'])
                elif u.path=='/workcontext/v2/project' and set(args)=={'project_id'}:result=engine.project(policy,args['project_id'])
                elif u.path=='/workcontext/v2/task' and set(args)=={'project_id','task_id'}:result=engine.task(policy,**{'project':args['project_id'],'task':args['task_id']})
                elif u.path=='/workcontext/v2/activity' and not set(args)-{'project_id','task_id'}:
                    if 'task_id' in args and 'project_id' not in args:raise Rejected('project_required',400)
                    result=engine.activity(policy,args.get('project_id'),args.get('task_id'))
                else:raise Rejected('route_denied')
            else:
                if u.path!='/workcontext/v2/handoff' or args:raise Rejected('read_only')
                if self.headers.get('Transfer-Encoding'):raise Rejected('invalid_body',400)
                try:length=int(self.headers.get('Content-Length','0'))
                except ValueError:raise Rejected('invalid_body',400)
                if not 0<length<=16384:raise Rejected('body_size',413)
                try:body=json.loads(self.rfile.read(length))
                except (ValueError,UnicodeError):raise Rejected('invalid_json',400)
                if not isinstance(body,dict) or set(body)!={'project_id','task_id','target_agent','target_device','token_budget'}:raise Rejected('invalid_fields',400)
                result=engine.handoff(policy,body['project_id'],body['task_id'],body['target_agent'],body['target_device'],body['token_budget'])
            self.reply(200,result)
        except Rejected as e:self.reply(e.status,{'error':e.reason})
        except Exception:self.reply(503,{'error':'context_unavailable'})

def serve(config):
    c=load(config)
    if c['bind']!='127.0.0.1':raise ValueError('loopback_only')
    Engine(config).sync()
    server=ThreadingHTTPServer((c['bind'],c['port']),Handler);server.daemon_threads=True;server.config=config
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever(poll_interval=.2)
    finally:server.server_close()

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True)
    run=s.add_parser('serve');run.add_argument('--config',required=True)
    migration=s.add_parser('migrate');migration.add_argument('--database',required=True);migration.add_argument('--direction',choices=['up','down'],default='up');migration.add_argument('--backup')
    a=p.parse_args()
    if a.command=='serve':serve(a.config)
    else:migrate(a.database,a.direction,a.backup)

if __name__=='__main__':main()
