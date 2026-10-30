from tests.client import DashboardClient
from orchestrator.main import app
from orchestrator import db,service as svc
from orchestrator.models import Collector,Host,HostGroup,Window,Attempt
from orchestrator.config import settings
from sqlalchemy import select


def auth(zone=None):
    headers={'Authorization':'Bearer '+settings.admin_token}
    if zone:headers['X-Tasker-Zone']=zone
    return headers


def test_workspace_reads_writes_and_cookie_are_isolated(database):
    with DashboardClient(app) as client:
        emea=client.get('/api/collectors',headers=auth('EMEA')).json()
        apac=client.get('/api/collectors',headers=auth('APAC')).json()
        assert emea and apac and not ({c['id'] for c in emea}&{c['id'] for c in apac})
        assert all(c['zone']=='EMEA' for c in emea)
        assert all(h['zone']=='EMEA' for h in client.get('/api/hosts',headers=auth('EMEA')).json())
        foreign=apac[0]
        body={k:foreign[k] for k in ('interval_seconds','timeout_seconds','max_retries','enabled','parameters')}
        assert client.put('/api/collectors/'+foreign['id'],headers=auth('EMEA'),json=body).status_code==404
        assert client.put('/api/hosts/nbu-apac-01/state',headers=auth('EMEA'),json={'enabled':False}).status_code==404
        assert client.post('/api/host-groups',headers=auth('EMEA'),json={'name':'Invalid','host_ids':['nbu-emea-01','nbu-apac-01']}).status_code==422
        assert client.post('/api/satellites',headers=auth('EMEA'),json={'id':'bad','zones':['APAC'],'scopes':['NetBackup']}).status_code==409
        assert client.post('/api/inventory/discover',headers=auth('EMEA'),json={'version':1,'hosts':[{'id':'bad-host','zone':'APAC'}]}).status_code==422
        page=client.get('/?view=overview&workspace=APAC')
        assert page.status_code==200 and client.cookies.get('workspace')=='APAC'
        assert 'nbu-emea-01' not in client.get('/?view=hosts').text
        # An older EMEA tab still addresses EMEA even after another tab switches the cookie.
        assert all(h['zone']=='EMEA' for h in client.get('/api/hosts',headers={'X-Tasker-Zone':'EMEA'}).json())
        assert client.get('/?view=overview&workspace=missing').status_code==404
        switched=client.post('/workspace',data={'zone_id':'EMEA','csrf':client.cookies.get('session')})
        assert switched.status_code==200 and client.cookies.get('workspace')=='EMEA'
        assert client.post('/workspace',data={'zone_id':'APAC','csrf':'wrong'}).status_code==403


def test_daily_grid_history_hour_filter_and_replay(database):
    with DashboardClient(app) as client:
        cid=client.get('/api/collectors',headers=auth('EMEA')).json()[0]['id']
        with db.transaction() as session:
            ids=svc.expected(session,'2026-10-15',cid,'nbu-emea-01',zone='EMEA')
            w=session.get(Window,ids[0]);w.status='SUCCESS';wid=w.id
            session.add(Attempt(id='old-failure',window_id=wid,worker_id='EMEA-SAT-1',number=1,status='FAILED',started_at=1,finished_at=2,lease_until=2,result=None,logs='',context={}))
        page=client.get('/?view=collectors&workspace=EMEA&date=2026-10-15').text
        assert 'Daily collection health' in page and '1 recovered' in page
        assert 'EXPECTED COLLECTIONS' in page and 'RUNNING / PENDING' in page
        assert 'hour-grid' not in page and 'view=runs' in page
        history=client.get('/?view=runs&workspace=EMEA&collector_id='+cid+'&date=2026-10-15&hour=0&status=SUCCESS').text
        assert 'Hour (UTC)' in history and '00:00–01:00 UTC' in history
        assert 'RECOVERED' in history and 'hour-grid' not in history and 'Run again' in history
        assert 'EXPECTED COLLECTIONS' not in history and 'RUNNING / PENDING' not in history
        first=client.post('/api/collections/retry',headers=auth('EMEA'),json={'ids':[wid],'mode':'rerun'})
        assert first.status_code==200 and first.json()['ids']==[wid]
        assert client.post('/api/collections/retry',headers=auth('EMEA'),json={'ids':[wid],'mode':'rerun'}).json()['ids']==[]
        assert client.post('/api/collections/retry',headers=auth('APAC'),json={'ids':[wid]}).status_code==404
        with db.Session() as session:
            assert session.get(Attempt,'old-failure') is not None


def test_schedule_list_create_and_details_are_separate(database):
    with DashboardClient(app) as client:
        group=client.post('/api/host-groups',headers=auth('EMEA'),json={'name':'Masters','host_ids':['nbu-emea-01']}).json()
        created=client.post('/api/schedules',headers=auth('EMEA'),json={'name':'Hourly jobs','collector_id':'netbackup_jobs','host_group_ids':[group['id']],'interval_seconds':3600})
        assert created.status_code==200,created.text
        rule=created.json();assert rule['zone']=='EMEA' and rule['zones']==['EMEA']
        listing=client.get('/?view=schedules&workspace=EMEA').text
        assert 'Add schedule' in listing and 'Hourly jobs' in listing and 'data-schedule=' not in listing
        assert 'group-launch' not in listing
        detail=client.get('/?view=schedules&workspace=EMEA&schedule_id='+rule['id']).text
        assert 'Schedule details' in detail and 'data-schedule="'+rule['id']+'"' in detail
        assert 'target_zones' in detail and '<legend>Zones' not in detail
        new=client.get('/?view=schedules&workspace=EMEA&new=1').text
        assert 'id="group-launch"' in new
        assert client.get('/?view=schedules&workspace=APAC&schedule_id='+rule['id']).status_code==404
        assert client.get('/api/schedules',headers=auth('APAC')).json()==[]


def test_named_users_have_zone_and_role_permissions(database):
    from orchestrator.models import User
    from orchestrator.security import issue_session,password_hash,credentials_match
    with db.Session.begin() as session:
        session.add(User(username='emea-reader',password_hash=password_hash('correct horse battery staple'),role='viewer',zones=['EMEA'],enabled=True))
    assert credentials_match('emea-reader','correct horse battery staple')
    assert not credentials_match('emea-reader','incorrect')
    with DashboardClient(app) as client:
        token=issue_session(username='emea-reader');client.cookies.set('session',token)
        assert client.get('/?view=overview&workspace=EMEA').status_code==200
        assert client.get('/?view=overview&workspace=APAC').status_code==404
        assert {z['id'] for z in client.get('/api/zones').json()}=={'EMEA'}
        assert client.post('/api/collections/retry',headers={'X-CSRF-Token':token},json={'ids':[]}).status_code==403
        with db.Session.begin() as session:session.get(User,'emea-reader').enabled=False
        assert client.get('/api/hosts').status_code==401


def test_online_backup_restore_drill_and_log_retention(database,tmp_path,monkeypatch):
    from scripts.backup import backup
    from scripts.retention import prune
    from orchestrator.config import settings
    from tests.test_core import one_window
    monkeypatch.setattr(settings,'database_url',str(database.url))
    wid=one_window('SUCCESS')
    with db.Session.begin() as session:session.add(Attempt(id='old-log',window_id=wid,worker_id='EMEA-SAT-1',number=1,status='SUCCESS',started_at=1,finished_at=2,lease_until=2,result={'status':'SUCCESS'},logs='old log',context={}))
    path=backup(tmp_path/'snapshot.db',check_restore=True)
    assert path.exists() and path.stat().st_mode&0o777==0o600
    assert prune(30)==1
    with db.Session() as session:assert session.get(Attempt,'old-log').logs=='old log'
    assert prune(30,apply=True)==1
    with db.Session() as session:
        attempt=session.get(Attempt,'old-log');assert attempt.logs=='' and attempt.result=={'status':'SUCCESS'}
        assert session.get(Window,wid) is not None
