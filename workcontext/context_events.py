"""Additive synthetic v2 envelope. No real source reader or automatic hooks."""
import hashlib
import re
from datetime import datetime,timezone

VERSION='workcontext.event.v2'
KINDS={'activity','decision','issue','task_status','validation'}
IDENTITY=re.compile(r'[A-Za-z0-9-]{1,96}')

def identifier(value):return isinstance(value,str) and bool(IDENTITY.fullmatch(value))

def validate_v2(e):
    fields={'schema_version','event_id','device_id','agent','session_id','source_event_id','event_type',
            'occurred_at','classification','grant_id','policy_version','content','context'}
    if not isinstance(e,dict) or set(e)!=fields:raise ValueError('invalid_v2_fields')
    for key in ['device_id','session_id','source_event_id','grant_id','policy_version']:
        if not identifier(e[key]):raise ValueError('invalid_identity')
    if e['schema_version']!=VERSION or e['classification']!='synthetic':raise ValueError('synthetic_only')
    if e['agent'] not in ['claude_code','codex_cli'] or e['event_type'] not in ['SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','Stop','SessionEnd']:raise ValueError('invalid_provider_or_event')
    if not e['session_id'].startswith('p2-') or len(e['session_id'])<=3 or e['content']!={'summary':'SYNTHETIC P2 fixture'}:raise ValueError('fixture_only')
    try:
        stamp=datetime.fromisoformat(e['occurred_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None or abs((datetime.now(timezone.utc)-stamp).total_seconds())>7*86400:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('invalid_time')
    eid=hashlib.sha256(':'.join(e[x] for x in ['device_id','agent','session_id','source_event_id']).encode()).hexdigest()
    if e['event_id']!=eid:raise ValueError('event_id_mismatch')
    ctx=e['context']
    if not isinstance(ctx,dict) or set(ctx)!={'project','task','thread','facts'}:raise ValueError('invalid_context')
    project=ctx['project']
    if not isinstance(project,dict) or not project or set(project)-{'project_id','git_remote','mapping_key'}:raise ValueError('invalid_project_reference')
    for key in ['project_id','mapping_key']:
        if key in project and not identifier(project[key]):raise ValueError('invalid_project_reference')
    if 'git_remote' in project and (not isinstance(project['git_remote'],str) or len(project['git_remote'])>512):raise ValueError('invalid_git_remote')
    for key in ['task','thread']:
        value=ctx[key]
        if not isinstance(value,dict) or set(value)!={'id','title'} or not identifier(value['id']):raise ValueError('invalid_'+key)
        if not isinstance(value['title'],str) or not value['title'].startswith('SYNTHETIC ') or len(value['title'])>160:raise ValueError('synthetic_title_required')
    facts=ctx['facts']
    if not isinstance(facts,list) or not 1<=len(facts)<=8:raise ValueError('invalid_facts')
    for f in facts:
        if not isinstance(f,dict) or set(f)!={'kind','key','text','state'}:raise ValueError('invalid_fact')
        if not isinstance(f['kind'],str) or f['kind'] not in KINDS or not identifier(f['key']):raise ValueError('invalid_fact_kind')
        if not isinstance(f['text'],str) or not f['text'].startswith('SYNTHETIC ') or len(f['text'])>500:raise ValueError('synthetic_fact_required')
        states={'activity':{'observed'},'decision':{'recorded'},'issue':{'open','resolved'},
                'task_status':{'in_progress','pending_validation','completed','blocked'},'validation':{'passed','failed','not_run'}}
        if not isinstance(f['state'],str) or f['state'] not in states[f['kind']]:raise ValueError('invalid_fact_state')
    return e
