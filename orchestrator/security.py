import hashlib,hmac,secrets,time,threading
from collections import OrderedDict
from fastapi import Request,HTTPException,Header
from .config import settings


def password_hash(password):
    salt=secrets.token_bytes(16)
    return 'scrypt$'+salt.hex()+'$'+hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()


def check_password(password,encoded):
    try:
        method,salt,digest=encoded.split('$')
        return method=='scrypt' and hmac.compare_digest(hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex(),digest)
    except (ValueError,TypeError):return False


def account(username):
    if username==settings.auth_username:
        return {'username':username,'secret':settings.auth_password,'role':'admin','zones':None,'root':True} if settings.auth_password else None
    from . import db
    from .models import User
    with db.Session() as session:
        user=session.get(User,username)
        if user and user.enabled:return {'username':username,'secret':user.password_hash,'role':user.role,'zones':user.zones,'root':False}


def token_username(token):
    import base64
    try:
        nonce=token.split('.')[1]
        return base64.urlsafe_b64decode(nonce.split('~')[0]+'===').decode() if '~' in nonce else settings.auth_username
    except (IndexError,ValueError,UnicodeError):return ''


def _session_key(purpose,username=None):
    user=account(username or settings.auth_username) if purpose=='session' else None
    credentials=(user['username']+'\0'+user['secret']+'\0'+user['role']+'\0'+str(user['zones'])) if user and not user['root'] else settings.auth_username+'\0'+settings.auth_password
    return hmac.new(settings.session_secret.encode(),(purpose+'\0'+credentials).encode(),hashlib.sha256).digest()


def issue_session(purpose='session',username=None):
    import base64
    nonce=secrets.token_hex(24)
    if username and username!=settings.auth_username:nonce=base64.urlsafe_b64encode(username.encode()).decode().rstrip('=')+'~'+nonce
    payload=f"{int(time.time())}.{nonce}"
    signature=hmac.new(_session_key(purpose,username),payload.encode(),hashlib.sha256).hexdigest()
    return payload+'.'+signature


def valid(token,purpose='session',max_age=43200):
    username=token_username(token) if purpose=='session' else None
    if purpose=='session' and not account(username):return False
    try:
        stamp,nonce,signature=token.split('.');payload=stamp+'.'+nonce
        return 0<=time.time()-int(stamp)<max_age and hmac.compare_digest(signature,hmac.new(_session_key(purpose,username),payload.encode(),hashlib.sha256).hexdigest())
    except (ValueError,AttributeError):return False


def web_token(request):
    token=request.cookies.get('session','')
    return token if valid(token) else ''


def principal(request):
    token=web_token(request)
    return account(token_username(token)) if token else None


def api_admin(authorization):
    return bool(settings.admin_token) and hmac.compare_digest(authorization.encode(),('Bearer '+settings.admin_token).encode())


def credentials_match(username,password):
    user=account(username)
    if not user:return False
    if not user['root']:return check_password(password,user['secret'])
    return bool(password and hmac.compare_digest(hashlib.sha256(password.encode()).digest(),hashlib.sha256(user['secret'].encode()).digest()))


def safe_next(value):
    if not value.startswith('/') or value.startswith('//') or any(c in value for c in ('\\','\r','\n','\t')):return '/?view=overview'
    if value.split('?',1)[0] in ('/login','/logout'):return '/?view=overview'
    return value


# Bounded, process-local protection for this single-process V0 master.
_login_failures=OrderedDict()
_login_lock=threading.Lock()
LOGIN_LIMIT=10
LOGIN_WINDOW=300


def login_limited(key):
    with _login_lock:
        attempts=[stamp for stamp in _login_failures.get(key,[]) if time.monotonic()-stamp<LOGIN_WINDOW]
        if attempts:_login_failures[key]=attempts
        else:_login_failures.pop(key,None)
        return len(attempts)>=LOGIN_LIMIT


def login_failure(key):
    with _login_lock:
        attempts=[stamp for stamp in _login_failures.get(key,[]) if time.monotonic()-stamp<LOGIN_WINDOW]
        _login_failures[key]=attempts+[time.monotonic()]
        _login_failures.move_to_end(key)
        while len(_login_failures)>2048:_login_failures.popitem(last=False)


def login_success(key):
    with _login_lock:_login_failures.pop(key,None)


def admin(request:Request,authorization:str=Header(default=''),x_csrf_token:str=Header(default='')):
    if api_admin(authorization):return
    token=web_token(request)
    if token and hmac.compare_digest(x_csrf_token.encode(),token.encode()):
        user=principal(request)
        actions=('/api/collections/retry','/api/collections/plan','/api/group-runs')
        if user['role'] not in ('admin','operator') or user['role']=='operator' and request.url.path not in actions:raise HTTPException(403,'Your role does not permit this action')
        if user['zones'] is not None and request.url.path.startswith(('/api/zones','/api/scopes')):raise HTTPException(403,'Only the global administrator manages zone workspaces')
        return
    raise HTTPException(401,'Administrator sign in required')


def worker_auth(worker_id,authorization):
    import os
    from . import db
    from .models import Worker
    with db.transaction() as session:
        worker=session.get(Worker,worker_id)
        if not worker:raise HTTPException(401,'Unknown satellite; provision it on the master first')
        if worker.token_hash:
            value=authorization.removeprefix('Bearer ')
            allowed=authorization.startswith('Bearer ') and hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(),worker.token_hash)
        else:
            expected=os.environ.get(worker.token_env,settings.worker_token)
            allowed=hmac.compare_digest(authorization.encode(),('Bearer '+expected).encode())
    if not allowed:raise HTTPException(401,'Invalid satellite token')
    return worker_id
