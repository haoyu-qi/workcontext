"""Owner-created, one-use pairing codes and revocable browser sessions.

Only hashes and service names are persisted here. Upstream secrets stay in
explicit private credential files and are reloaded on every authenticated call.
"""
import hashlib,json,os,secrets,sqlite3,time
from contextlib import contextmanager
from http.cookies import SimpleCookie,CookieError
from pathlib import Path
from .service import load,Rejected,active

COOKIE='wc_session'
def digest(value):return hashlib.sha256(value.encode()).hexdigest()

class Sessions:
    def __init__(self,config):
        self.config=load(config) if not isinstance(config,dict) else config
        self.auth=self.config.get('auth')
        if not self.auth:raise Rejected('pairing_not_configured',503)

    @contextmanager
    def db(self):
        path=Path(self.auth['database']).expanduser();path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Create privately before SQLite opens the database.
        fd=os.open(str(path),os.O_CREAT|os.O_RDWR,0o600);os.close(fd)
        if os.name!='nt':path.chmod(0o600)
        db=sqlite3.connect(str(path),timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS pairing (hash TEXT PRIMARY KEY, expires REAL, services TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL, services TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS failures (at REAL)')
        try:
            with db:yield db
        finally:db.close()

    def credentials(self):
        result={}
        for service,path in self.auth.get('credentials',{}).items():
            if service not in ['memory','context']:raise Rejected('invalid_auth_config',503)
            value=load(Path(path).expanduser());token=value.get('token')
            if not isinstance(token,str) or not token or len(token)>4000:raise Rejected('credential_unavailable',503)
            if value.get('expires_at') and not active({'enabled':True,'expires_at':value['expires_at']}):raise Rejected('credential_expired',401)
            result[service]=token
        if not result:raise Rejected('credential_unavailable',503)
        return result

    def fingerprints(self):return {name:digest(token) for name,token in self.credentials().items()}

    def create_pair(self):
        code=secrets.token_hex(6).upper();expires=time.time()+300
        services=json.dumps(self.fingerprints(),sort_keys=True)
        with self.db() as db:
            db.execute('DELETE FROM pairing');db.execute('INSERT INTO pairing VALUES (?,?,?)',(digest(code),expires,services))
        return {'code':'-'.join(code[i:i+4] for i in range(0,12,4)),'expires_at':expires,'valid_seconds':300}

    def redeem(self,code):
        if not isinstance(code,str) or len(code)>64:raise Rejected('invalid_pairing_code',401)
        code=code.replace('-','').replace(' ','').upper();now=time.time();error=None;result=None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM failures WHERE at<?',(now-60,))
            db.execute('DELETE FROM sessions WHERE expires<=?',(now,))
            if db.execute('SELECT count(*) FROM failures').fetchone()[0]>=10:
                error=Rejected('pairing_rate_limited',429)
            else:
                row=db.execute('SELECT expires,services FROM pairing WHERE hash=?',(digest(code),)).fetchone()
                if not row or row[0]<=now or json.loads(row[1])!=self.fingerprints():
                    db.execute('INSERT INTO failures VALUES (?)',(now,));error=Rejected('invalid_pairing_code',401)
                else:
                    seconds=self.auth.get('session_seconds',28800)
                    if type(seconds) is not int or not 300<=seconds<=86400:raise Rejected('invalid_auth_config',503)
                    token=secrets.token_urlsafe(32);expires=now+seconds
                    db.execute('DELETE FROM pairing WHERE hash=?',(digest(code),))
                    db.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),expires,row[1]))
                    result={'token':token,'expires_at':expires,'services':list(json.loads(row[1])),'seconds':seconds}
        if error:raise error
        return result

    def token(self,cookie):
        try:
            values=SimpleCookie();values.load(cookie or '')
            return values[COOKIE].value if COOKIE in values else ''
        except CookieError:return ''

    def resolve(self,cookie):
        token=self.token(cookie)
        if not token or len(token)>100:raise Rejected('session_expired',401)
        with self.db() as db:row=db.execute('SELECT expires,services FROM sessions WHERE hash=?',(digest(token),)).fetchone()
        if not row or row[0]<=time.time():raise Rejected('session_expired',401)
        credentials=self.credentials();fingerprints={name:digest(value) for name,value in credentials.items()}
        if json.loads(row[1])!=fingerprints:raise Rejected('session_expired',401)
        return {'expires_at':row[0],'credentials':credentials}

    def logout(self,cookie):
        with self.db() as db:db.execute('DELETE FROM sessions WHERE hash=?',(digest(self.token(cookie)),))

    def revoke_all(self):
        with self.db() as db:
            db.execute('DELETE FROM sessions');db.execute('DELETE FROM pairing')
        return {'status':'revoked'}

def session_cookie(token,seconds,secure):
    return COOKIE+'='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age='+str(seconds)+('; Secure' if secure else '')
