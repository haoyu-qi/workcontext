import json,os,tempfile,unittest
from pathlib import Path
from workcontext.credentials import export

class Credentials(unittest.TestCase):
    def test_export_separates_services_and_does_not_print_or_copy_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);memory=root/'owner.json';context=root/'consumer.json';dest=root/'connection.json'
            memory.write_text(json.dumps({'token':'memory-secret','expires_at':'2099-01-01T00:00:00Z','unrelated_secret':'never-copy'}))
            context.write_text(json.dumps({'token':'context-secret'}))
            result=export(dest,memory,context);value=json.loads(dest.read_text())
            self.assertEqual(value,{'workcontext_credentials':1,'memory':{'token':'memory-secret','expires_at':'2099-01-01T00:00:00Z'},'context':{'token':'context-secret'}})
            self.assertNotIn('secret',json.dumps(result))
            if os.name!='nt':self.assertEqual(dest.stat().st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):export(dest,memory,context)
            if os.name!='nt':
                link=root/'link.json';link.symlink_to(root/'absent.json')
                with self.assertRaises(FileExistsError):export(link,memory,context)
                self.assertFalse((root/'absent.json').exists())

    def test_invalid_and_expired_credentials_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'token.json';dest=root/'connection.json'
            for value in [{'token':'private-token','expires_at':'2000-01-01T00:00:00Z'},{'token':42},{'token':'bad\ncredential'}]:
                source.write_text(json.dumps(value))
                with self.assertRaises(ValueError):export(dest,source,source)
                self.assertFalse(dest.exists())
