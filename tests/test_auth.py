"""Real anonymous clients test the authentication boundary independently of business tests."""
import re
import pytest
from fastapi.testclient import TestClient
from orchestrator.main import app
from orchestrator.config import settings
from orchestrator import security

@pytest.fixture
def client(database):
    security._login_failures.clear()
    with TestClient(app,follow_redirects=False) as c:yield c
    security._login_failures.clear()

def sign_in(c,**changes):
    page=c.get('/login')
    challenge=re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
    data={'username':settings.auth_username,'password':settings.auth_password,'csrf':challenge}
    return c.post('/login',data={**data,**changes})

def test_anonymous_boundary_and_worker_auth(client):
    for path in ['/','/?view=workers','/docs','/redoc','/collections/anything']:
        r=client.get(path);assert r.status_code==303 and r.headers['location'].startswith('/login?next=')
    for path in ['/api/workers','/api/hosts','/api/collectors','/openapi.json']:
        assert client.get(path).status_code==401
    assert client.get('/health').status_code==200
    assert client.get('/static/login.css').status_code==200
    assert client.post('/api/workers/claim',json={'worker_id':'EMEA-SAT-1'}).status_code==401
    assert client.get('/api/workers',headers={'Authorization':'Bearer '+settings.admin_token}).status_code==200

def test_login_cookie_csrf_and_logout(client):
    r=sign_in(client,next='/?view=workers');assert r.status_code==303 and r.headers['location']=='/?view=workers'
    cookie=r.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Max-Age=43200' in cookie
    assert client.get('/').status_code==200
    session=client.cookies.get('session')
    assert client.post('/api/collections/retry',json={'ids':[]}).status_code==401
    assert client.post('/api/collections/retry',headers={'X-CSRF-Token':session},json={'ids':[]}).status_code==200
    assert client.post('/logout',data={'csrf':'wrong'}).status_code==403
    assert client.get('/').status_code==200
    r=client.post('/logout',data={'csrf':session});assert r.status_code==303 and r.headers['location']=='/login'
    assert not client.cookies.get('session') and client.get('/').status_code==303

def test_invalid_login_and_csrf(client):
    assert client.post('/login',data={'username':settings.auth_username,'password':settings.auth_password}).status_code==403
    r=sign_in(client,password='wrong');assert r.status_code==401 and 'Invalid username or password.' in r.text
    assert 'session=' not in r.headers.get('set-cookie','')
    assert sign_in(client,username='unknown').status_code==401
    assert sign_in(client,password=settings.admin_token).status_code==401
    assert sign_in(client,csrf='forged').status_code==403

@pytest.mark.parametrize('destination',['https://example.com','//example.com','/\\example.com','/login','/logout','/\r\nexample.com'])
def test_safe_redirect(client,destination):
    assert sign_in(client,next=destination).headers['location']=='/?view=overview'

def test_throttle(client):
    for _ in range(10):assert sign_in(client,password='wrong').status_code==401
    assert sign_in(client).status_code==429

def test_session_expiry_tampering_rotation_and_unconfigured(client,monkeypatch):
    token=security.issue_session();assert security.valid(token)
    assert not security.valid(token+'x') and not security.valid('invalid')
    original=security.time.time()
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(security.time,'time',lambda:original+43201)
        assert not security.valid(token)
    token=security.issue_session();monkeypatch.setattr(settings,'auth_password','rotated')
    assert not security.valid(token)
    monkeypatch.setattr(settings,'auth_password','')
    assert not security.valid(security.issue_session())
    assert 'Login is not configured' in client.get('/login').text

def test_secure_cookie_on_https(database):
    with TestClient(app,base_url='https://testserver',follow_redirects=False) as c:
        assert 'Secure' in sign_in(c).headers['set-cookie']
