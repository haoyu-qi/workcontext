"""Portable foreground supervisor for a service bundle; use OS service managers for login/boot."""
import json,os,signal,subprocess,time,sys
from pathlib import Path

def order(services):
    names={s['name']:s for s in services}
    if len(names)!=len(services):raise ValueError('duplicate_service')
    done=[];visiting=set()
    def visit(name):
        if name not in names:raise ValueError('missing_dependency')
        if name in done:return
        if name in visiting:raise ValueError('dependency_cycle')
        visiting.add(name)
        for dependency in names[name].get('depends_on',[]):visit(dependency)
        visiting.remove(name);done.append(name)
    for name in names:visit(name)
    return [names[n] for n in done]

def run(config):
    path=Path(config).resolve();c=json.loads(path.read_text());services=order(c['services']);children={};last_start={};handles={};stop=False
    root=path.parent;logs=root/c.get('log_directory','logs');logs.mkdir(parents=True,exist_ok=True,mode=0o700)
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    try:
        while not stop:
            for service in services:
                name=service['name'];child=children.get(name)
                if child and child.poll() is None:continue
                if any(d not in children or children[d].poll() is not None for d in service.get('depends_on',[])):continue
                if time.monotonic()-last_start.get(name,0)<max(1,service.get('restart_seconds',10)):continue
                if child and not service.get('restart',True):raise RuntimeError('service_exited: '+name)
                argv=[sys.executable if value=='@python' else value for value in service['argv']]
                if not isinstance(argv,list) or not argv or not all(isinstance(x,str) for x in argv):raise ValueError('argv_array_required')
                for handle in handles.pop(name,()):handle.close()
                stdout=(logs/(name+'.stdout.log')).open('ab');stderr=(logs/(name+'.stderr.log')).open('ab');handles[name]=(stdout,stderr)
                env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1'
                options={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
                children[name]=subprocess.Popen(argv,cwd=root/service.get('cwd','.'),env=env,stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,shell=False,**options);last_start[name]=time.monotonic()
                (logs/'status.json').write_text(json.dumps({n:{'pid':p.pid,'running':p.poll() is None} for n,p in children.items()},indent=2))
            time.sleep(.3)
    finally:
        for name,child in reversed(list(children.items())):
            if child.poll() is None:
                try:
                    if os.name=='nt':subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=5)
                    else:os.killpg(child.pid,signal.SIGTERM)
                    child.wait(timeout=8)
                except (ProcessLookupError,subprocess.TimeoutExpired):child.kill();child.wait()
        for pair in handles.values():
            for handle in pair:handle.close()
