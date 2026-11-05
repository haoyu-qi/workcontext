import json,tempfile,unittest
from pathlib import Path
from workcontext import skill_sync as sync
class Skills(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source';self.target=self.root/'target';(self.source/'example').mkdir(parents=True);(self.source/'example/SKILL.md').write_text('---\nname: example\ndescription: Example workflow.\n---\nFirst version.\n')
 def tearDown(self):self.tmp.cleanup()
 def test_sync_update_conflict_and_rollback(self):
  m=sync.build(self.source,['example']);r=sync.apply(self.source,self.target,m,'macos');self.assertEqual(r['changes'][0]['action'],'install')
  (self.source/'example/SKILL.md').write_text('Second version.');new=sync.build(self.source,['example']);r2=sync.apply(self.source,self.target,new,'macos');self.assertEqual((self.target/'example/SKILL.md').read_text(),'Second version.')
  sync.rollback(r2['rollback']);self.assertIn('First version',(self.target/'example/SKILL.md').read_text());sync.rollback(r['rollback']);self.assertFalse((self.target/'example').exists())
 def test_local_changes_preserved_and_bad_checksums_rejected(self):
  m=sync.build(self.source,['example']);r=sync.apply(self.source,self.target,m,'macos');(self.target/'example/SKILL.md').write_text('local edits')
  (self.source/'example/SKILL.md').write_text('source edit');new=sync.build(self.source,['example'])
  with self.assertRaisesRegex(ValueError,'conflict'):sync.apply(self.source,self.target,new,'macos')
  with self.assertRaisesRegex(ValueError,'conflict'):sync.rollback(r['rollback'])
  self.assertEqual((self.target/'example/SKILL.md').read_text(),'local edits')
  with self.assertRaisesRegex(ValueError,'checksum'):sync.plan(self.source,self.target,m,'macos')
 def test_platform_private_files_and_symlinks(self):
  m=sync.build(self.source,['example'],{'example':['macos']});self.assertEqual(sync.plan(self.source,self.target,m,'windows')['changes'][0]['action'],'skip_platform')
  (self.source/'example/.env').write_text('private')
  with self.assertRaises(ValueError):sync.build(self.source,['example'])
  (self.source/'example/.env').unlink()
  try:(self.source/'example/link').symlink_to(self.root/'outside')
  except (OSError,NotImplementedError):return
  with self.assertRaises(ValueError):sync.build(self.source,['example'])
 def test_traversal_and_secret_rejected(self):
  with self.assertRaises(ValueError):sync.build(self.source,['../outside'])
  (self.source/'example/SKILL.md').write_text('ghp_'+'a'*36)
  with self.assertRaises(ValueError):sync.build(self.source,['example'])
if __name__=='__main__':unittest.main()
