"""Single entry point for WorkContext services, sources, skills and inventory."""
import argparse,json,platform,runpy,sys,time
from pathlib import Path

def output(value):print(json.dumps(value,ensure_ascii=False,indent=2))
def main():
    p=argparse.ArgumentParser(prog='workcontext');sub=p.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve');serve.add_argument('service',choices=['receiver','context','execution','web','memory']);serve.add_argument('--config',required=True)
    worker=sub.add_parser('worker');worker.add_argument('--config',required=True);worker.add_argument('--once',action='store_true')
    mcp=sub.add_parser('mcp');mcp.add_argument('surface',choices=['context','execution','memory']);mcp.add_argument('--config',required=True)
    relay=sub.add_parser('relay');relay.add_argument('--config',required=True);relay.add_argument('--once',action='store_true')
    runtime=sub.add_parser('services');runtime.add_argument('--config',required=True)
    logs=sub.add_parser('logs');logs.add_argument('action',choices=['inventory','collect','watch']);logs.add_argument('--config',required=True);logs.add_argument('--include-content',action='store_true');logs.add_argument('--max-records',type=int);logs.add_argument('--interval',type=int,default=30)
    skill=sub.add_parser('skills');skill.add_argument('action',choices=['manifest','plan','apply','rollback']);skill.add_argument('--source');skill.add_argument('--target');skill.add_argument('--manifest');skill.add_argument('--names',nargs='+');skill.add_argument('--platform',default={'Darwin':'macos','Windows':'windows','Linux':'linux'}.get(platform.system(),'linux'));skill.add_argument('--record')
    sub.add_parser('selfcheck')
    a=p.parse_args()
    if a.command=='serve':
        if a.service=='receiver':from .service import run;run(a.config)
        elif a.service=='context':from .context_service import serve;serve(a.config)
        elif a.service=='execution':from .execution import serve;serve(a.config)
        elif a.service=='memory':from .memory_service import serve;serve(a.config)
        else:from .web_service import serve;serve(a.config)
    elif a.command=='worker':from .execution_worker import run;run(a.config,a.once)
    elif a.command=='relay':from .memory_relay import run;output(run(a.config,a.once))
    elif a.command=='mcp':
        if a.surface=='execution':from .execution_mcp import run
        elif a.surface=='memory':from .memory_client import run
        else:from .mcp_server import run
        run(a.config)
    elif a.command=='services':from .runtime import run;run(a.config)
    elif a.command=='logs':
        from .source_logs import inventory,collect
        config=json.loads(Path(a.config).read_text(encoding='utf-8'))
        if a.action=='inventory':output(inventory(config['sources']))
        else:
            while True:
                output(collect(config,a.include_content,a.max_records))
                if a.action!='watch':break
                time.sleep(max(1,a.interval))
    elif a.command=='skills':
        from . import skill_sync
        if a.action=='rollback':
            if not a.record:p.error('--record is required')
            output(skill_sync.rollback(a.record));return
        if not a.source:p.error('--source is required')
        if a.action=='manifest':
            if not a.names:p.error('--names is required')
            value=skill_sync.build(a.source,a.names)
            if a.manifest:Path(a.manifest).write_text(json.dumps(value,indent=2))
            output(value)
        else:
            if not a.target or not a.manifest:p.error('--target and --manifest are required')
            value=json.loads(Path(a.manifest).read_text())
            output((skill_sync.plan if a.action=='plan' else skill_sync.apply)(a.source,a.target,value,a.platform))
    elif a.command=='selfcheck':
        from . import execution,execution_worker,context_engine,web_service,source_logs,skill_sync,long_memory,memory_service,memory_client,memory_relay
        from importlib import resources
        assert resources.files('workcontext').joinpath('web/index.html').is_file()
        assert resources.files('workcontext').joinpath('migrations/p2/001-up.sql').is_file()
        output({'service':'workcontext','version':'0.4.0','status':'ok'})
if __name__=='__main__':main()
