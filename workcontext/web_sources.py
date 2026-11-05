"""Read-only browser access to the owning device's existing relay outbox."""
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from .long_memory import terms
from .service import load,token_ok,digest,active,Rejected

class LocalSources:
    def __init__(self,config,token):
        self.path=Path(config['database']).expanduser().resolve()
        credential=load(config['token_file'])
        if not token_ok(token,digest(credential['token'])) or not active({'enabled':True,'expires_at':credential.get('expires_at','')}):
            raise Rejected('unauthorized',401)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=5)
        try:yield db
        finally:db.close()

    def stats(self):
        with self.db() as db:
            queue=dict(db.execute("SELECT state,count(*) FROM outbox WHERE state IN ('pending','delivered') GROUP BY state"))
            agents=[{'device':device,'agent':agent,'count':count} for device,agent,count in db.execute("SELECT json_extract(body,'$.device'),json_extract(body,'$.agent'),count(*) FROM outbox WHERE state IN ('pending','delivered') GROUP BY 1,2")]
        return {'source':'local','records':sum(queue.values()),'notes':None,'queue':queue,'agents':agents}

    def search(self,query):
        if not 1<=len(query)<=500:raise Rejected('invalid_query',400)
        needles=list(dict.fromkeys([query.lower()]+terms(query)))
        where=' OR '.join("instr(lower(json_extract(body,'$.text')),?)>0 OR instr(lower(json_extract(body,'$.project')),?)>0" for _ in needles)
        values=[value for needle in needles for value in [needle,needle]]
        with self.db() as db:
            rows=db.execute("SELECT body,state FROM outbox WHERE state IN ('pending','delivered') AND ("+where+") ORDER BY rowid DESC LIMIT 8",values).fetchall()
        return {'source':'local','query':query,'matches':[dict(json.loads(body),type='records',delivery_state=state,excerpt=json.loads(body)['text'][:1800]) for body,state in rows]}

    def get(self,identity):
        with self.db() as db:row=db.execute("SELECT body,state FROM outbox WHERE id=? AND state IN ('pending','delivered')",(identity,)).fetchone()
        if not row:raise Rejected('not_found',404)
        return dict(json.loads(row[0]),type='records',delivery_state=row[1],source='local')
