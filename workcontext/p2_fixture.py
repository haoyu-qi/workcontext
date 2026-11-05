"""Explicit synthetic multi-Agent scenario; never reads transcripts or repositories."""
from datetime import datetime,timezone,timedelta
from .service import canonical,digest

CATALOG=[{'project_id':'p2-demo','name':'SYNTHETIC Shared Project',
          'git_remotes':['https://git.example.invalid/team/demo.git'],'mapping_keys':['p2-map']},
         {'project_id':'p2-private','name':'SYNTHETIC Shared Project',
          'git_remotes':['https://git.example.invalid/team/private.git'],'mapping_keys':['private-map']}]

def scenario(c,run_id):
    base=datetime.now(timezone.utc)-timedelta(seconds=20)
    def fact(kind,key,state,text):return dict(kind=kind,key=key,state=state,text='SYNTHETIC '+text)
    definitions=[
        ('codex_cli',{'git_remote':'git@git.example.invalid:team/demo.git'},'shared-task','design','SessionStart',
         [fact('activity','design','observed','Codex designed context projection'),fact('task_status','status','in_progress','Implementation started')]),
        ('codex_cli',{'mapping_key':'p2-map'},'shared-task','design','PostToolUse',
         [fact('decision','storage','recorded','Use a separate SQLite context database; preserve P1 ledger')]),
        ('claude_code',{'project_id':'p2-demo'},'shared-task','review','SessionStart',
         [fact('activity','review','observed','Claude reviewed context evidence and permissions')]),
        ('claude_code',{'mapping_key':'p2-map'},'shared-task','review','Stop',
         [fact('task_status','status','pending_validation','Cross-Agent review complete; restart validation pending'),
          fact('issue','restart','open','Restart recovery requires independent verification'),fact('validation','validation','not_run','No restart verification yet')]),
        ('codex_cli',{'project_id':'p2-demo'},'finished-task','delivery','SessionEnd',
         [fact('task_status','status','completed','Synthetic schema checks completed'),fact('validation','validation','passed','Synthetic schema checks passed')]),
        ('claude_code',{'project_id':'p2-private'},'secret-task','private','Stop',
         [fact('decision','private','recorded','PRIVATE-P2-SENTINEL must stay outside shared handoff')])]
    result=[]
    for i,(agent,project,task,thread,kind,facts) in enumerate(definitions):
        raw={'schema_version':'workcontext.event.v2','session_id':'p2-'+run_id+'-'+agent.replace('_','-'),
             'source_event_id':run_id+'-'+str(i),'hook_event_name':kind,'cwd':c['allowed_roots'][0],
             'occurred_at':(base+timedelta(seconds=i)).isoformat().replace('+00:00','Z'),
             'classification':'synthetic','prompt':'SYNTHETIC P2 fixture',
             'context':{'project':project,'task':{'id':task,'title':'SYNTHETIC '+task},
                        'thread':{'id':thread,'title':'SYNTHETIC '+thread},'facts':facts}}
        result.append((agent,raw))
    return result
