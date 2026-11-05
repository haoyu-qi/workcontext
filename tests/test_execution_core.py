"""Routing and transactional lifecycle tests against actual SQLite."""
import copy,json,secrets,sys,tempfile,time,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workcontext.execution import Scheduler,database
from workcontext.service import digest,Rejected

class Routing(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.p={'enabled':True,'expires_at':expiry,'projects':['demo'],'capabilities':['check']}
        self.c={'database':str(self.root/'jobs.sqlite'),'nodes':{n:{**self.p,'os':os,'priority':i,'token_hash':digest(n),'capacity':1} for i,(n,os) in enumerate([('mini','macos'),('macbook','macos'),('windows','windows')])},'operators':{'operator':{**self.p,'token_hash':digest('op')}},'readers':{'reader':{**self.p,'token_hash':digest('read')}}}
        self.path=self.root/'config.json';self.save();self.s=Scheduler(self.path)
        for n in ['mini','macbook']:self.heartbeat(n)
    def tearDown(self):self.temp.cleanup()
    def save(self):self.path.write_text(json.dumps(self.c))
    def heartbeat(self,n):return self.s.operate('worker',n,self.c['nodes'][n],'POST','/heartbeat',{'os':self.c['nodes'][n]['os'],'capabilities':['check'],'projects':{'demo':'v1'}})
    def submit(self,**kw):
        b={'project':'demo','revision':'v1','capability':'check','required_os':'any','idempotency_key':secrets.token_hex(8)};b.update(kw)
        return self.s.operate('operator','operator',self.p,'POST','/submit',b)
    def claim(self,n):return self.s.operate('worker',n,self.c['nodes'][n],'POST','/claim',{})['job']
    def state(self):return self.s.operate('reader','reader',self.p,'GET','/state')
    def test_routing_locality_os_and_capacity(self):
        a=self.submit();self.assertIsNone(self.claim('macbook'));self.assertEqual(self.claim('mini')['id'],a['id'])
        b=self.submit(workspace_node='macbook');self.assertEqual(self.claim('macbook')['id'],b['id'])
        self.submit(required_os='windows');waiting=self.state()['jobs'][0]
        self.assertEqual(waiting['eligible_nodes'],[]);self.assertEqual(waiting['waiting_reasons']['mini'],'os_mismatch');self.assertEqual(waiting['waiting_reasons']['windows'],'worker_offline')
        self.heartbeat('windows');self.assertIsNotNone(self.claim('windows'))
    def test_preferred_and_busy_fallback(self):
        a=self.submit(preferred_node='macbook');self.assertEqual(self.claim('macbook')['id'],a['id'])
        b=self.submit(preferred_node='macbook');self.assertEqual(self.claim('mini')['id'],b['id'])
    def test_idempotency_readonly_and_revocation(self):
        a=self.submit(idempotency_key='same');b=self.submit(idempotency_key='same');self.assertEqual(a['id'],b['id'])
        with self.assertRaises(Rejected):self.submit(idempotency_key='same',required_os='windows')
        with self.assertRaises(Rejected):self.s.operate('reader','reader',self.p,'POST','/submit',a['request'])
        with self.assertRaises(Rejected):self.submit(project='private')
        self.c['operators']['operator']['enabled']=False;self.save()
        with self.assertRaises(Rejected):self.s.policy('op')
    def test_claim_replay_result_idempotency_and_attempt_binding(self):
        a=self.submit();j=self.claim('mini');self.assertEqual(j,self.claim('mini'))
        pair={'id':j['id'],'attempt':j['attempt']}
        with self.assertRaises(Rejected):self.s.operate('worker','macbook',self.c['nodes']['macbook'],'POST','/start',pair)
        self.s.operate('worker','mini',self.c['nodes']['mini'],'POST','/start',pair)
        r={'state':'succeeded','exit_code':0,'stdout':'SYNTHETIC evidence','stderr':'','duration':.1,'revision':'v1','os':'macos','capability':'check','truncated':False}
        body={**pair,'result':r};a=self.s.operate('worker','mini',self.c['nodes']['mini'],'POST','/result',body);self.assertFalse(a['duplicate'])
        self.assertTrue(self.s.operate('worker','mini',self.c['nodes']['mini'],'POST','/result',body)['duplicate'])
        with self.assertRaises(Rejected):self.s.operate('worker','mini',self.c['nodes']['mini'],'POST','/result',{**pair,'result':{**r,'stdout':'changed'}})
        self.assertIsNone(self.claim('mini'))
    def test_revision_offline_and_recovery_never_reexecute(self):
        self.submit(revision='missing');self.assertEqual(self.state()['jobs'][0]['eligible_nodes'],[])
        a=self.submit();j=self.claim('mini')
        with database(self.c['database']) as db:db.execute('UPDATE jobs SET updated=? WHERE id=?',(time.time()-200,j['id']))
        self.assertEqual(self.claim('mini')['state'],'recovery_required')
        self.assertEqual(self.claim('mini')['attempt'],j['attempt'])
    def test_cancel_and_arbitrary_payload_rejected(self):
        j=self.submit();self.s.operate('operator','operator',self.p,'POST','/cancel',{'id':j['id']});self.assertIsNone(self.claim('mini'))
        with self.assertRaises(Rejected):self.submit(command='touch /tmp/should-not-run')
        with self.assertRaises(Rejected):self.submit(workspace_node='unknown')
        with self.assertRaises(Rejected):self.s.operate('worker','mini',self.c['nodes']['mini'],'POST','/heartbeat',{'os':'windows','capabilities':['check'],'projects':{'demo':'v1'}})
if __name__=='__main__':unittest.main(verbosity=2)
