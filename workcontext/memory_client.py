"""Private memory API/MCP bridge. No token value appears in command arguments."""
import argparse,json,sys
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError,URLError
from .service import load,canonical,NoRedirect

def api(config,path,body=None):
    c=load(config) if not isinstance(config,dict) else config
    bases=list(dict.fromkeys([c['url']]+c.get('fallback_urls',[])))
    if not 1<=len(bases)<=3:raise ValueError('too_many_destinations')
    for base in bases:
        u=urlsplit(base)
        if u.username or u.password or u.fragment or u.query or (u.scheme!='https' and not(u.scheme=='http' and u.hostname=='127.0.0.1')):raise ValueError('unsafe_destination')
    headers={'Authorization':'Bearer '+load(c['token_file'])['token'],'Content-Type':'application/json'}
    for index,base in enumerate(bases):
        req=Request(base.rstrip('/')+path,headers=headers,data=canonical(body).encode() if body is not None else None)
        try:
            with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=15) as r:return json.loads(r.read(2097152))
        except HTTPError:raise
        except (URLError,OSError):
            if index==len(bases)-1:raise

TOOLS=[{'name':name,'description':desc,'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False},'annotations':{'readOnlyHint':readonly,'destructiveHint':False}} for name,desc,props,required,readonly in [
 ('workcontext_memory.search','Search Mini durable memories and cited work records.',{'query':{'type':'string'}},['query'],True),
 ('workcontext_memory.get','Get a memory/source by ID.',{'id':{'type':'string'}},['id'],True),
 ('workcontext_memory.remember','Save a stable memory on Mini.',{k:{'type':'string'} for k in ['content','title','kind','project','note_id']},['content'],False)]]
def run(config):
    for raw in sys.stdin.buffer:
        request=None
        try:
            if len(raw)>65536:raise ValueError()
            request=json.loads(raw)
            if 'id' not in request:continue
            method=request.get('method');p=request.get('params',{})
            if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'workcontext-memory','version':'0.4.0'}}
            elif method=='ping':result={}
            elif method=='tools/list':result={'tools':TOOLS}
            elif method=='tools/call':
                name=p.get('name');args=p.get('arguments',{});tool=next((t for t in TOOLS if t['name']==name),None)
                try:
                    if not tool or not isinstance(args,dict) or set(args)-set(tool['inputSchema']['properties']) or not set(tool['inputSchema']['required'])<=set(args):raise ValueError()
                    if name.endswith('.search'):value=api(config,'/memory/search?'+urlencode({'q':args['query']}))
                    elif name.endswith('.get'):value=api(config,'/memory/get?'+urlencode(args))
                    else:value=api(config,'/memory/remember',args)
                    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'structuredContent':value,'isError':False}
                except Exception:result={'content':[{'type':'text','text':'Memory unavailable or request refused.'}],'isError':True}
            else:raise ValueError()
            out={'jsonrpc':'2.0','id':request['id'],'result':result}
        except Exception:out={'jsonrpc':'2.0','id':request.get('id') if isinstance(request,dict) else None,'error':{'code':-32600,'message':'Invalid request'}}
        print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);run(p.parse_args().config)
