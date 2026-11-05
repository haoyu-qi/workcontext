import json,tempfile,unittest,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from workcontext.web_auth import Sessions,session_cookie
from workcontext.service import Rejected

class BrowserSessions(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.token=self.root/'token.json';self.token.write_text(json.dumps({'token':'SYNTHETIC-private-reader','expires_at':'2099-01-01T00:00:00Z'}))
        self.config={'auth':{'database':str(self.root/'sessions.sqlite'),'credentials':{'memory':str(self.token)}}}
        self.sessions=Sessions(self.config)
    def tearDown(self):self.temp.cleanup()
    def pair(self):
        value=self.sessions.redeem(self.sessions.create_pair()['code'])
        return value,session_cookie(value['token'],value['seconds'],False)
    def test_one_use_cookie_and_no_plaintext_persistence(self):
        code=self.sessions.create_pair()['code'];value=self.sessions.redeem(code)
        with self.assertRaises(Rejected):self.sessions.redeem(code)
        cookie=session_cookie(value['token'],value['seconds'],True)
        for flag in ['HttpOnly','Secure','SameSite=Strict','Path=/']:self.assertIn(flag,cookie)
        self.assertEqual(self.sessions.resolve(cookie)['credentials']['memory'],'SYNTHETIC-private-reader')
        raw=(self.root/'sessions.sqlite').read_bytes()
        for secret in [code.replace('-',''),value['token'],'SYNTHETIC-private-reader']:self.assertNotIn(secret.encode(),raw)
        Sessions(self.config).logout(cookie)
        with self.assertRaises(Rejected):self.sessions.resolve(cookie)
    def test_expiry_rotation_and_admin_revocation(self):
        value,cookie=self.pair()
        with self.sessions.db() as db:db.execute('UPDATE sessions SET expires=?',(time.time()-1,))
        with self.assertRaises(Rejected):self.sessions.resolve(cookie)
        code=self.sessions.create_pair()['code']
        with self.sessions.db() as db:db.execute('UPDATE pairing SET expires=0')
        with self.assertRaises(Rejected):self.sessions.redeem(code)
        value,cookie=self.pair();self.token.write_text(json.dumps({'token':'rotated'}))
        with self.assertRaises(Rejected):self.sessions.resolve(cookie)
        value,cookie=self.pair();self.sessions.revoke_all()
        with self.assertRaises(Rejected):self.sessions.resolve(cookie)
    def test_rate_limit_persists_and_concurrent_redeem_is_atomic(self):
        code=self.sessions.create_pair()['code']
        def attempt(_):
            try:self.sessions.redeem(code);return True
            except Rejected:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(attempt,range(2))),1)
        for _ in range(9):
            with self.assertRaises(Rejected):self.sessions.redeem('wrong')
        with self.assertRaises(Rejected) as error:Sessions(self.config).redeem(self.sessions.create_pair()['code'])
        self.assertEqual(error.exception.status,429)
