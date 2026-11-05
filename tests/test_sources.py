import json,shutil,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from contextlib import closing
from workcontext.source_logs import collect,inventory
class Sources(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.logs=self.root/'logs';self.logs.mkdir();self.c={'database':str(self.root/'archive.sqlite'),'sources':[{'agent':'dsh','path':str(self.logs)}]}
 def tearDown(self):self.tmp.cleanup()
 def test_jsonl_dedupe_append_and_content_upgrade(self):
  p=self.logs/'session.jsonl';p.write_text(json.dumps({'type':'session','id':'s1'})+'\n'+json.dumps({'type':'tool','tool_name':'read','authorization':'Bearer '+'s'*30,'reasoning':'private reasoning'})+'\n')
  self.assertEqual(collect(self.c)['inserted'],2);self.assertEqual(collect(self.c)['unchanged_files'],1)
  r=collect(self.c,True);self.assertEqual(r['updated'],2)
  with closing(sqlite3.connect(self.c['database'])) as db, db:
   rows=[json.loads(x[0]) for x in db.execute('SELECT body FROM source_records')]
  self.assertNotIn('authorization',rows[1]['record']);self.assertNotIn('reasoning',rows[1]['record'])
  with p.open('a') as f:f.write(json.dumps({'type':'completed'})+'\n')
  self.assertEqual(collect(self.c,True)['inserted'],1)
 def test_zstd_and_corrupt_input_visibility(self):
  if not shutil.which('zstd'):self.skipTest('zstd binary optional')
  raw=self.logs/'s.jsonl';raw.write_text(json.dumps({'type':'session','id':'compressed'})+'\n');compressed=self.logs/'s.jsonl.zstd'
  subprocess.run(['zstd','-q','-f',str(raw),'-o',str(compressed)],check=True)
  raw.unlink();self.assertEqual(collect(self.c)['inserted'],1);self.assertEqual(inventory(self.c['sources'])[0]['files'],1)
  compressed.write_bytes(b'invalid');self.assertTrue(collect(self.c)['errors'])
 def test_hermes_readonly_sqlite(self):
  p=self.root/'state.db'
  with closing(sqlite3.connect(p)) as db, db:
   db.execute('CREATE TABLE messages (id INTEGER,session_id TEXT,role TEXT,content TEXT)');db.execute('INSERT INTO messages VALUES (1,?,?,?)',('s','assistant','hello'))
  before=p.read_bytes();self.c['sources']=[{'agent':'hermes','path':str(p),'format':'hermes-sqlite'}]
  self.assertEqual(collect(self.c,True)['inserted'],1);self.assertEqual(before,p.read_bytes())
 def test_incomplete_record_recovered_next_scan(self):
  p=self.logs/'s.jsonl';p.write_text('{"type":"message"}\n{"type":')
  self.assertEqual(collect(self.c)['inserted'],1);self.assertTrue(collect(self.c)['errors'])
  p.write_text('{"type":"message"}\n{"type":"finished"}\n');self.assertEqual(collect(self.c)['inserted'],1)
if __name__=='__main__':unittest.main()
