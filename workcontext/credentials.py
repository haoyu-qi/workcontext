"""Explicit owner-side export of existing credentials; never prints token values."""
import json,re
from pathlib import Path
from .service import private_write,active

def export(output,memory_token_file=None,context_token_file=None):
    home=Path.home()
    paths={'memory':Path(memory_token_file).expanduser() if memory_token_file else home/'workcontext-memory/state/owner-token.json',
           'context':Path(context_token_file).expanduser() if context_token_file else home/'workcontext-p2/state/consumer-token.json'}
    if not context_token_file and not paths['context'].is_file():
        paths['context']=Path.cwd()/'.p2/private/consumer-token.json'
    credentials={}
    for service,path in paths.items():
        if not path.is_file():
            if (service=='memory' and memory_token_file) or (service=='context' and context_token_file):raise ValueError(service+'_credential_file_missing')
            continue
        value=json.loads(path.read_text(encoding='utf-8-sig'));token=value.get('token')
        if not isinstance(token,str) or len(token)>4000 or not re.fullmatch(r'[A-Za-z0-9._~+/-]{1,4000}=*',token):raise ValueError(service+'_credential_invalid')
        if value.get('expires_at') and not active({'enabled':True,'expires_at':value['expires_at']}):raise ValueError(service+'_credential_expired')
        credentials[service]={'token':token}
        if value.get('expires_at'):credentials[service]['expires_at']=value['expires_at']
    if not credentials:raise ValueError('no_credentials_found')
    destination=Path(output).expanduser().absolute()
    # Exclusive creation refuses existing files, including symlinks.
    private_write(destination,{'workcontext_credentials':1,**credentials})
    return {'file':str(destination),'services':list(credentials),'status':'exported'}
