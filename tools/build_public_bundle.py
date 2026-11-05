"""Create a source bundle from the Git index, rejecting private paths and credential patterns."""
import hashlib,json,re,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BLOCKED_ROOTS={'.git','.p0','.p1','.p2','.web','.execution','.integration','.release','agentpulse','dist','build','state','logs'}
SECRET=re.compile(rb'(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{25,}|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})')

def audit(paths):
    result=[]
    for name in paths:
        p=Path(name)
        if p.parts[0] in BLOCKED_ROOTS or p.name in ['.env','SOUL.md','MEMORY.md','USER.md','auth.json'] or p.suffix.lower() in ['.pem','.key','.sqlite','.db','.jsonl']:
            raise ValueError('Private file in publication: '+name)
        source=ROOT/p
        if source.is_symlink():raise ValueError('Symlink in publication: '+name)
        raw=source.read_bytes()
        if SECRET.search(raw):raise ValueError('Credential-like content: '+name)
        result.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    return result

def main():
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0');names=[n for n in names if n]
    if not names:raise ValueError('Stage the curated public files first')
    manifest=audit(names);out=ROOT/'dist';out.mkdir(exist_ok=True)
    target=out/'workcontext-services-0.3.0.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for item in manifest:z.write(ROOT/item['path'],'workcontext-services/'+item['path'])
        z.writestr('workcontext-services/PUBLICATION-MANIFEST.json',json.dumps({'version':'0.3.0','files':manifest},indent=2))
    (out/'PUBLICATION-MANIFEST.json').write_text(json.dumps({'version':'0.3.0','files':manifest},indent=2))
    print(json.dumps({'source_bundle':str(target),'files':len(manifest),'secret_scan':'passed','sha256':hashlib.sha256(target.read_bytes()).hexdigest()},indent=2))
if __name__=='__main__':main()
