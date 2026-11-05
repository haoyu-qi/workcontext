"""Independent MCP stdio server. Five query tools, no execution surface."""
import argparse
import json
import sys
from urllib.parse import urlencode
from urllib.error import HTTPError
from .service import http,load

def schema(properties,required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
S={'type':'string','minLength':1,'maxLength':200}
TOOLS=[
 {'name':'workcontext.search','description':'Search authorized synthetic activity with source evidence.','inputSchema':schema({'query':S},['query'])},
 {'name':'workcontext.get_project','description':'Get authorized project state and cited facts.','inputSchema':schema({'project_id':S},['project_id'])},
 {'name':'workcontext.get_task','description':'Get a task across Agent sessions; completion and validation are separate.','inputSchema':schema({'project_id':S,'task_id':S},['project_id','task_id'])},
 {'name':'workcontext.get_activity','description':'Get authorized recent work with event/device/session evidence.','inputSchema':schema({'project_id':S,'task_id':S},[])},
 {'name':'workcontext.create_handoff','description':'Compute a read-only task context package within caller and recipient permissions and UTF-8 byte budget. No commands or task mutations.','inputSchema':schema({'project_id':S,'task_id':S,'target_agent':S,'target_device':S,'token_budget':{'type':'integer','minimum':256,'maximum':65536}},['project_id','task_id','target_agent','target_device','token_budget'])}
]
for tool in TOOLS:tool['annotations']={'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}

def call(config,name,args):
    tool=next((t for t in TOOLS if t['name']==name),None)
    if not tool or not isinstance(args,dict):raise ValueError('unknown_tool')
    definition=tool['inputSchema']
    if set(args)-set(definition['properties']) or not set(definition['required'])<=set(args):raise ValueError('invalid_arguments')
    for key,value in args.items():
        if key=='token_budget':
            if isinstance(value,bool) or not isinstance(value,int):raise ValueError('invalid_budget')
        elif not isinstance(value,str) or not 1<=len(value)<=200:raise ValueError('invalid_arguments')
    c=load(config);token=load(c['token_file'])['token'];base=c['url'].rstrip('/')
    if name=='workcontext.create_handoff':return http(base+'/workcontext/v2/handoff',token,args)
    route={'workcontext.search':'search','workcontext.get_project':'project','workcontext.get_task':'task','workcontext.get_activity':'activity'}[name]
    query={'q':args['query']} if route=='search' else args
    return http(base+'/workcontext/v2/'+route+('?' +urlencode(query) if query else ''),token)

def run(config):
    for raw in sys.stdin.buffer:
        request=None
        try:
            if len(raw)>65536:raise ValueError('message_too_large')
            request=json.loads(raw)
            if not isinstance(request,dict) or request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):raise ValueError('invalid_request')
            if 'id' not in request:continue
            method=request['method'];params=request.get('params',{})
            if method=='initialize':
                version=params.get('protocolVersion','2024-11-05')
                if version not in ['2024-11-05','2025-03-26','2025-06-18','2025-11-25']:version='2024-11-05'
                result={'protocolVersion':version,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'workcontext','version':'2.0.0'}}
            elif method=='ping':result={}
            elif method=='tools/list':result={'tools':TOOLS}
            elif method=='tools/call':
                try:
                    value=call(config,params.get('name'),params.get('arguments',{}))
                    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'structuredContent':value,'isError':False}
                except HTTPError as error:
                    try:reason=json.loads(error.read(4096)).get('error','query_refused')
                    except Exception:reason='query_refused'
                    result={'content':[{'type':'text','text':str(reason)}],'isError':True}
                except Exception:result={'content':[{'type':'text','text':'invalid_or_unavailable_query'}],'isError':True}
            else:
                print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue
            response={'jsonrpc':'2.0','id':request['id'],'result':result}
        except Exception:
            response={'jsonrpc':'2.0','id':request.get('id') if isinstance(request,dict) else None,'error':{'code':-32600,'message':'Invalid request'}}
        print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);run(p.parse_args().config)
