import hashlib,json,os,secrets,subprocess,sys,tempfile,time,unittest
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.error import HTTPError
from workcontext.long_memory import Store
from workcontext.memory_relay import scan,upload,connect,text_from
from workcontext.memory_client import api
from workcontext.service import digest
from workcontext.source_logs import records
from test_p1 import port,wait
ROOT=Path(__file__).resolve().parents[1]
class Memory(unittest.TestCase):
 def test_oversized_record_preserves_gap_and_later_messages(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'session.jsonl';p.write_text(json.dumps({'content':'x'*1048577})+'\n'+json.dumps({'role':'user','content':'later work survives'})+'\n')
   rows=list(records(p,'jsonl',skip_oversized=True))
   self.assertEqual(rows[0][1]['type'],'workcontext_omitted_record');self.assertEqual(rows[1][0],2)
   self.assertEqual(text_from(rows[1][1]),'later work survives')
 def test_persistence_versions_citations_and_cleaning(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'memory.sqlite';s=Store(path);r={'id':'a'*64,'device':'macbook','agent':'codex','session':'s','project':'p','ordinal':'1','text':'项目采用 SQLite，验证仍待进行','occurred':'','metadata':{'cwd':'/approved'}}
   self.assertEqual(s.ingest([r])['inserted'],1);self.assertEqual(s.ingest([r])['duplicates'],1)
   with self.assertRaises(ValueError):s.ingest([{**r,'text':'conflict'}])
   n=s.remember('项目采用 SQLite','项目决定','decision',evidence=[r['id']]);s=Store(path);self.assertEqual(s.get(n['id'])['version'],1)
   updated=s.remember('项目改用 PostgreSQL','项目决定','decision',evidence=[r['id']],note_id=n['id']);self.assertEqual(updated['version'],2)
   self.assertIn(n['id'],s.context('项目')['context']);self.assertTrue(s.search('SQLite')['matches'])
   with self.assertRaises(ValueError):s.remember('invented','bad',evidence=['unknown'])
   secret='sk-'+'x'*30;note=s.remember('api_key='+secret,'scrub');self.assertNotIn(secret,note['content'])
 def test_http_relay_scope_durable_ack_and_read_only(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);workspace=root/'work';workspace.mkdir();logs=root/'logs';logs.mkdir();file=logs/'session.jsonl'
   file.write_text(json.dumps({'type':'session_meta','payload':{'id':'s1','cwd':str(workspace)}})+'\n'+json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'记住此项目采用 SQLite'}]}})+'\n')
   owner=secrets.token_urlsafe();uploader=secrets.token_urlsafe();reader=secrets.token_urlsafe();expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();base='http://127.0.0.1:'+str(port())
   cfg={'bind':'127.0.0.1','port':int(base.rsplit(':',1)[1]),'database':str(root/'memory.sqlite'),'clients':{n:{'enabled':True,'expires_at':expiry,'token_hash':digest(token),**policy} for n,token,policy in [('owner',owner,{'role':'owner'}),('reader',reader,{'role':'reader'}),('relay',uploader,{'role':'ingest','device':'macbook','agents':['codex'],'roots':[str(workspace.resolve())]})]}}
   config=root/'service.json';config.write_text(json.dumps(cfg));token=root/'upload.json';token.write_text(json.dumps({'token':uploader}));ownerfile=root/'owner.json';ownerfile.write_text(json.dumps({'token':owner}));readerfile=root/'reader.json';readerfile.write_text(json.dumps({'token':reader}))
   client={'url':base,'token_file':str(ownerfile)};readonly={'url':base,'token_file':str(readerfile)};relay={'url':base,'token_file':str(token),'device':'macbook','outbox':str(root/'outbox.sqlite'),'allowed_roots':[str(workspace)],'sources':[{'agent':'codex','path':str(logs)}]}
   child=subprocess.Popen([sys.executable,'-m','workcontext.memory_service','--config',str(config)],cwd=ROOT)
   try:
    wait(lambda:api(client,'/health')['persistent'])
    dead=port()
    while dead==cfg['port']:dead=port()
    self.assertEqual(api({**client,'url':'http://127.0.0.1:'+str(dead),'fallback_urls':[base]},'/stats')['records'],0)
    with self.assertRaises(ValueError):api({**client,'fallback_urls':['http://example.invalid']},'/stats')
    self.assertEqual(scan(relay)['queued'],1);self.assertEqual(upload(relay)['delivered'],1);self.assertEqual(upload(relay)['delivered'],0)
    hits=api(client,'/memory/search?q=SQLite')['matches'];self.assertEqual(hits[0]['agent'],'codex');self.assertEqual(api(client,'/stats')['records'],1)
    with self.assertRaises(HTTPError):api(readonly,'/memory/remember',{'content':'not allowed'})
    with closing(connect(relay)) as db:db.execute("UPDATE outbox SET state='pending'");db.commit()
    self.assertEqual(upload(relay)['delivered'],1);self.assertEqual(api(client,'/stats')['records'],1)
    cfg['clients']['relay']['enabled']=False;config.write_text(json.dumps(cfg))
    with closing(connect(relay)) as db:db.execute("UPDATE outbox SET state='pending'");db.commit()
    with self.assertRaises(HTTPError):upload(relay)
   finally:child.terminate();child.wait(timeout=10)
 def test_private_roles_and_tool_outputs_not_relayed(self):
  self.assertEqual(text_from({'role':'system','content':'private instructions'}),'')
  self.assertEqual(text_from({'type':'reasoning','text':'hidden reasoning'}),'')
  self.assertEqual(text_from({'type':'message','role':'assistant','channel':'analysis','content':'hidden reasoning'}),'')
  self.assertEqual(text_from({'type':'function_call_output','output':'secret'}),'')
  self.assertEqual(text_from({'role':'assistant','content':'validated result'}),'validated result')
if __name__=='__main__':unittest.main()
