"""Separate, scoped execution MCP. Does not change the P2 read-only tool surface."""
import argparse,json,sys
from urllib.parse import urlencode
from .service import load,http
S={'type':'string','minLength':1,'maxLength':128}
def tool(name,description,props,required,readonly=True):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False},'annotations':{'readOnlyHint':readonly,'destructiveHint':False,'openWorldHint':False}}
TOOLS=[tool('execution.nodes','List registered worker capabilities, online state, queued jobs and execution summaries.',{},[]),
       tool('execution.get','Read full execution result and evidence for a job.',{'id':S},['id']),
       tool('execution.submit','Submit an explicitly requested job using a pre-registered capability. Required OS and workspace constraints are enforced; no arbitrary shell.',{**{k:S for k in ['project','revision','capability','idempotency_key','workspace_node','preferred_node']},'required_os':{'enum':['any','windows','macos','linux']},'allowed_nodes':{'type':'array','items':S}},['project','revision','capability','required_os','idempotency_key'],False),
       tool('execution.cancel','Request cancellation of an authorized execution job.',{'id':S},['id'],False)]
def call(config,name,args):
    t=next((x for x in TOOLS if x['name']==name),None)
    if not t or not isinstance(args,dict) or set(args)-set(t['inputSchema']['properties']) or not set(t['inputSchema']['required'])<=set(args):raise ValueError('invalid_arguments')
    c=load(config);token=load(c['token_file'])['token'];base=c['url'].rstrip('/')
    route={'execution.nodes':'/state','execution.get':'/job','execution.submit':'/submit','execution.cancel':'/cancel'}[name]
    if name=='execution.get':route+='?'+urlencode(args)
    return http(base+route,token,args if name in ['execution.submit','execution.cancel'] else None)
def run(config):
    for raw in sys.stdin.buffer:
        request=None
        try:
            if len(raw)>65536:raise ValueError()
            request=json.loads(raw)
            if not isinstance(request,dict) or request.get('jsonrpc')!='2.0':raise ValueError()
            if 'id' not in request:continue
            method=request.get('method');p=request.get('params',{})
            if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'workcontext-execution','version':'1.0.0'}}
            elif method=='ping':result={}
            elif method=='tools/list':result={'tools':TOOLS}
            elif method=='tools/call':
                try:
                    value=call(config,p.get('name'),p.get('arguments',{}));result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'structuredContent':value,'isError':False}
                except Exception:result={'content':[{'type':'text','text':'Execution request refused or service unavailable; check scope, OS, capability and revision.'}],'isError':True}
            else:raise ValueError()
            output={'jsonrpc':'2.0','id':request['id'],'result':result}
        except Exception:output={'jsonrpc':'2.0','id':request.get('id') if isinstance(request,dict) else None,'error':{'code':-32600,'message':'Invalid request'}}
        print(json.dumps(output,ensure_ascii=False),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);run(p.parse_args().config)
