"""Real SQLite, HTTP and stdio processes; synthetic data only."""
import copy,json,os,secrets,sqlite3,subprocess,sys,tempfile,unittest,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workcontext.service import adapt_hook,canonical,digest,init,load,store,database,Rejected
from workcontext.context_engine import Engine,Resolver,normalize_git,migrate
from workcontext.p2_fixture import CATALOG,scenario
from test_p1 import port,wait,request,P1Integration

class P2Engine(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        init(self.root/'collector','collector','macbook','http://127.0.0.1:1')
        self.cc=load(self.root/'collector/config.json')
        self.source=self.root/'source.sqlite'
        with database(self.source):pass
        self.source_config=self.root/'source.json'
        self.source_config.write_text(canonical({'devices':{'macbook':{'grant':self.cc['grant']}}}))
        self.policy={'enabled':True,'expires_at':self.cc['grant']['expires_at'],'projects':['p2-demo'],
                     'source_devices':['macbook'],'target_agents':['codex_cli'],'target_devices':['macbook'],'max_budget':16000}
        self.token=secrets.token_urlsafe(32)
        self.c={'bind':'127.0.0.1','port':port(),'database':str(self.root/'context.sqlite'),
                'source_database':str(self.source),'source_config':str(self.source_config),'catalog':copy.deepcopy(CATALOG),
                'readers':{'test':dict(self.policy,token_hash=digest(self.token))},
                'targets':{'codex_cli@macbook':dict(self.policy)}}
        self.config=self.root/'config.json';self.save();migrate(self.c['database']);self.e=Engine(self.config)
        self.events=[]
        for agent,raw in scenario(self.cc,'test'+uuid.uuid4().hex[:8]):
            e=adapt_hook(raw,agent,self.cc);store(self.source,e);self.events.append(e)
    def tearDown(self):self.tmp.cleanup()
    def save(self):self.config.write_text(canonical(self.c))
    def test_resolver(self):
        r=Resolver(CATALOG)
        for v in ['git@git.example.invalid:team/demo.git','ssh://git@git.example.invalid/team/demo.git','https://GIT.example.invalid/team/demo/']:
            self.assertEqual(r.resolve({'git_remote':v}),('p2-demo','resolved'))
        self.assertEqual(r.resolve({'project_id':'p2-demo','mapping_key':'private-map'})[0],None)
        self.assertEqual(r.resolve({'name':'SYNTHETIC Shared Project'})[0],None)
        self.assertEqual(r.resolve({'project_id':'unknown'})[0],None)
        with self.assertRaises(ValueError):normalize_git('https://user:password@example.invalid/a')
        with self.assertRaises(ValueError):Resolver(CATALOG+[dict(CATALOG[0],project_id='duplicate')])
    def test_cross_agent_task_and_project(self):
        task=self.e.task(self.policy,'p2-demo','shared-task')
        self.assertEqual(task['current_state']['state'],'pending_validation')
        self.assertEqual({x['agent'] for x in task['sessions']},{'codex_cli','claude_code'})
        self.assertEqual(task['threads'],['design','review'])
        self.assertEqual(len(task['unresolved_issues']),1)
        self.assertEqual(task['decisions'][0]['evidence'][0]['event_id'],self.events[1]['event_id'])
        project=self.e.project(self.policy,'p2-demo')
        self.assertEqual([t['task_id'] for t in project['completed_tasks']],['finished-task'])
        self.assertEqual([t['task_id'] for t in project['pending_validation_tasks']],['shared-task'])
        self.assertNotIn('PRIVATE-P2-SENTINEL',canonical(project))
    def test_grant_and_reader_revocation(self):
        self.source_config.write_text(canonical({'devices':{'macbook':{'grant':dict(self.cc['grant'],enabled=False)}}}))
        self.assertEqual(self.e.activity(self.policy)['activity'],[])
        with self.assertRaises(Rejected):self.e.task(dict(self.policy,enabled=False),'p2-demo','shared-task')
    def test_handoff_permissions_and_budget(self):
        h=self.e.handoff(self.policy,'p2-demo','shared-task','codex_cli','macbook',12000)
        self.assertEqual(h['current_state']['state'],'pending_validation');self.assertTrue(h['evidence'])
        self.assertLessEqual(len(canonical(h).encode()),h['budget_upper_bound']);self.assertLessEqual(h['budget_upper_bound'],12000)
        self.assertNotIn('PRIVATE-P2-SENTINEL',canonical(h))
        for args in [('p2-private','secret-task','codex_cli','macbook',12000),('p2-demo','shared-task','hermes','macbook',12000),('p2-demo','shared-task','codex_cli','macbook',256)]:
            with self.assertRaises(Rejected):self.e.handoff(self.policy,*args)
        self.c['targets']['codex_cli@macbook']['source_devices']=[];self.save()
        with self.assertRaises(Rejected):self.e.handoff(self.policy,'p2-demo','shared-task','codex_cli','macbook',12000)
    def test_idempotent_checkpoint_rebuild_and_restart(self):
        before=self.source.read_bytes();self.e.sync();self.e.sync()
        with sqlite3.connect(self.c['database']) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM work_events').fetchone()[0],6)
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],3)
        self.assertEqual(Engine(self.config).task(self.policy,'p2-demo','shared-task')['task_id'],'shared-task')
        self.c['catalog'][0]['mapping_keys']=[];self.save();self.e.sync()
        with sqlite3.connect(self.c['database']) as db:self.assertEqual(db.execute("SELECT count(*) FROM work_events WHERE resolution='unresolved_unknown_mapping'").fetchone()[0],2)
        self.assertEqual(before,self.source.read_bytes())
    def test_migration_rollback_refuses_p1_and_preserves_backup(self):
        with self.assertRaisesRegex(ValueError,'refusing_p1'):migrate(self.source)
        self.e.sync()
        with self.assertRaises(ValueError):migrate(self.c['database'],'down')
        backup=self.root/'backup.sqlite';migrate(self.c['database'],'down',backup)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM work_events').fetchone()[0],6)
        migrate(self.c['database']);self.e.sync()
        self.assertEqual(self.e.task(self.policy,'p2-demo','shared-task')['current_state']['state'],'pending_validation')
    def test_newer_status_does_not_inherit_old_validation(self):
        e=copy.deepcopy(self.events[4]);e['source_event_id']='later';e['event_id']=digest(':'.join(e[k] for k in ['device_id','agent','session_id','source_event_id']))
        e['occurred_at']=datetime.now(timezone.utc).isoformat()
        e['context']['facts']=[dict(kind='task_status',key='status',state='completed',text='SYNTHETIC new revision')]
        store(self.source,e)
        t=self.e.task(self.policy,'p2-demo','finished-task')
        self.assertEqual(t['validation']['state'],'not_run');self.assertTrue(t['validation']['stale'])
    def test_invalid_v2_and_legacy_remains_unassociated(self):
        agent,raw=scenario(self.cc,'malformed')[0]
        missing=dict(raw);del missing['context']
        with self.assertRaises(Rejected):adapt_hook(missing,agent,self.cc)
        malformed=copy.deepcopy(raw);malformed['context']['facts'][0]['kind']=[]
        with self.assertRaises(Rejected):adapt_hook(malformed,agent,self.cc)
        legacy={k:v for k,v in raw.items() if k not in ['schema_version','context']}
        legacy.update(session_id='p1-legacy',prompt='SYNTHETIC P1 fixture')
        store(self.source,adapt_hook(legacy,agent,self.cc));self.e.sync()
        with sqlite3.connect(self.c['database']) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM work_events WHERE resolution='unresolved_no_reference'").fetchone()[0],1)
        self.assertEqual(len(self.e.activity(self.policy)['activity']),5)
    def test_unicode_search_and_offset_activity_order(self):
        agent,raw=scenario(self.cc,'unicode')[0]
        raw['context']['facts']=[{'kind':'activity','key':'unicode','state':'observed','text':'SYNTHETIC 中文上下文检索'}]
        # UTC+08 representation of an older instant must precede all newer UTC facts.
        raw['occurred_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).astimezone(timezone(timedelta(hours=8))).isoformat()
        store(self.source,adapt_hook(raw,agent,self.cc))
        matches=self.e.search(self.policy,'中文上下文')['matches'];self.assertEqual(len(matches),1)
        activities=self.e.project(self.policy,'p2-demo')['recent_activity']
        self.assertEqual(activities[0]['text'],'SYNTHETIC 中文上下文检索')
    def test_http_mcp_processes_and_control_denial(self):
        log=open(self.root/'service.log','wb')
        child=subprocess.Popen([sys.executable,'-m','workcontext.context_service','serve','--config',str(self.config)],cwd=ROOT,stdout=log,stderr=log)
        try:
            base='http://127.0.0.1:'+str(self.c['port']);wait(lambda:request(base,'/health')[0]==200)
            self.assertEqual(request(base,'/workcontext/v2/project?project_id=p2-demo')[0],401)
            self.assertEqual(request(base,'/workcontext/v2/project?project_id=p2-private',self.token)[0],404)
            self.assertEqual(request(base,'/execute',self.token,{'command':'bad'})[0],403)
            self.assertEqual(request(base,'/workcontext/v2/activity',self.token,headers={'Origin':'https://bad.invalid'})[0],403)
            tokenfile=self.root/'token.json';tokenfile.write_text(canonical({'token':self.token}))
            mc=self.root/'mcp.json';mc.write_text(canonical({'url':base,'token_file':str(tokenfile)}))
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26'}},
                      {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'workcontext.create_handoff','arguments':{'project_id':'p2-demo','task_id':'shared-task','target_agent':'codex_cli','target_device':'macbook','token_budget':12000}}},
                      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'shell','arguments':{}}}]
            result=subprocess.run([sys.executable,'-m','workcontext.mcp_server','--config',str(mc)],input='\n'.join(map(json.dumps,requests))+'\n',capture_output=True,text=True,cwd=ROOT,timeout=15)
            self.assertEqual(result.returncode,0);rows=[json.loads(x) for x in result.stdout.splitlines()]
            self.assertEqual(len(rows[1]['result']['tools']),5)
            self.assertEqual(rows[2]['result']['structuredContent']['task_id'],'shared-task')
            self.assertFalse(rows[2]['result']['isError']);self.assertTrue(rows[3]['result']['isError'])
        finally:child.terminate();child.wait(timeout=10);log.close()

class P2Transport(P1Integration):
    # Reuse actual upstream + central + Collector process harness, not the inherited P1 tests.
    def test_p2_transport(self):
        for agent,raw in scenario(self.collector,uuid.uuid4().hex[:12]):
            self.assertEqual(self.post(raw,agent)[0],200)
            self.assertTrue(self.post(raw,agent)[1]['duplicate'])
        wait(lambda:len(self.rows())==6);wait(lambda:all(r[2]=='delivered' for r in self.rows()))
        self.assertEqual(len(self.rows(self.collector['database'])),6)

# Prevent discovery from running the imported base suite twice; P1 runs separately.
for name in list(vars(P1Integration)):
    if name.startswith('test_'):setattr(P2Transport,name,None)
del P1Integration
if __name__=='__main__':unittest.main(verbosity=2)
