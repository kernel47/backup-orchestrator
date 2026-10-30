import asyncio,time
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from orchestrator import db,service as svc
from orchestrator.models import *
from orchestrator.config import settings

def one_window(status='PENDING'):
    with db.transaction() as s:
        ids=svc.expected(s,'2026-10-15','netbackup_jobs',host='nbu-emea-01')
        w=next(s.get(Window,i) for i in ids if s.get(Window,i).period_start=='2026-10-15T10:00:00Z');w.status=status
        return w.id

def payload(status='SUCCESS'):
    return {'status':'SUCCESS' if status=='SUCCESS' else 'FAILURE','summary':'Test report','collected_at':'2026-10-20T10:00:00Z','details':{'records_collected':10}}

def test_inventory_and_expected(database):
    with db.transaction() as s:
        assert len(list(s.scalars(select(Host))))==15
        assert len(list(s.scalars(select(Worker))))==6
        ids=svc.expected(s,'2026-10-15','netbackup_jobs',zone='EMEA')
        assert len(ids)==576 and ids==svc.expected(s,'2026-10-15','netbackup_jobs',zone='EMEA')
        assert all(r['status']=='MISSING' for r in svc.rows(s,{'date':'2026-10-15'}))
        assert len(svc.expected(s,'2026-10-15','netbackup_policies',zone='EMEA'))==4

def test_atomic_capacity_and_zone(database):
    one_window()
    assert svc.claim('APAC-SAT-1') is None
    with ThreadPoolExecutor(8) as pool:claimed=list(pool.map(lambda _:svc.claim('EMEA-SAT-1'),range(8)))
    assert sum(c is not None for c in claimed)==1

def test_result_history_idempotence_and_manual_retry_budget(database):
    cid=one_window();task=svc.claim('EMEA-SAT-1')
    svc.result(task['task_id'],'EMEA-SAT-1',payload())
    assert svc.result(task['task_id'],'EMEA-SAT-1',payload())['idempotent']
    with pytest.raises(ValueError):svc.result(task['task_id'],'EMEA-SAT-2',payload())
    with db.transaction() as s:s.get(Window,cid).status='FAILED'
    assert svc.retry({},[cid])==[cid] and svc.retry({},[cid])==[]
    task2=svc.claim('EMEA-SAT-1');assert task2['period_start']==task['period_start']
    svc.result(task2['task_id'],'EMEA-SAT-1',payload())
    with db.transaction() as s:
        assert s.get(Window,cid).retry_base==1
        assert len(svc.rows(s,{'status':'SUCCESS'})[0]['attempts'])==2

def test_fallback_timeout_offline_and_stale(database):
    cid=one_window()
    with db.transaction() as s:s.get(Worker,'EMEA-SAT-1').heartbeat=time.time()
    assert svc.claim('EMEA-SAT-2') is None
    with db.transaction() as s:s.get(Worker,'EMEA-SAT-1').heartbeat=0
    task=svc.claim('EMEA-SAT-2')
    with db.transaction() as s:s.get(Attempt,task['task_id']).lease_until=0
    with pytest.raises(ValueError):svc.result(task['task_id'],'EMEA-SAT-2',payload())
    with db.transaction() as s:
        svc.recover(s);assert s.get(Attempt,task['task_id']).status=='TIMEOUT'
        s.get(Window,cid).available_at=0
    task2=svc.claim('EMEA-SAT-2')
    with db.transaction() as s:
        s.get(Worker,'EMEA-SAT-2').heartbeat=0;svc.recover(s)
        assert s.get(Attempt,task2['task_id']).status=='WORKER_OFFLINE'

def test_backoff_and_retry_exhaustion(database):
    cid=one_window()
    for n in range(3):
        task=svc.claim('EMEA-SAT-1');assert task
        svc.result(task['task_id'],'EMEA-SAT-1',payload('FAILED'))
        with db.transaction() as s:
            w=s.get(Window,cid);assert w.status==('FAILED' if n==2 else 'PENDING');w.available_at=0
    assert svc.claim('EMEA-SAT-1') is None

def test_persistence_cadence_snapshot_and_disable(database):
    cid=one_window('MISSING')
    with db.transaction() as s:
        c=s.get(Collector,'netbackup_jobs');c.interval_seconds=3600;c.parameters={'custom':'new'}
        svc.expected(s,'2026-10-15','netbackup_jobs',host='nbu-emea-01')
        assert len(svc.rows(s,{'collector_id':'netbackup_jobs'}))==144
    database.dispose()
    assert svc.retry({},[cid])==[cid]
    with db.transaction() as s:
        w=s.get(Window,cid);assert w.snapshot['interval_seconds']==600 and w.snapshot['parameters']['custom']=='new'
        s.get(Host,w.host_id).enabled=False
    assert svc.claim('EMEA-SAT-1') is None

@pytest.mark.parametrize('simulation,expected',[('SUCCESS','SUCCESS'),('FAILED','FAILURE'),('UNKNOWN','UNKNOWN'),('ERROR','FAILURE'),('TIMEOUT','TIMEOUT')])
def test_real_script_subprocess(simulation,expected):
    from worker.executor import execute
    task={'script':'fake','collector':'Jobs','collector_id':'netbackup_jobs','host':'test','period_start':'2026-10-15T10:00:00Z','period_end':'2026-10-15T10:10:00Z','timeout_seconds':2,'parameters':{'delay_seconds':.01,'simulation':simulation}}
    result,logs=asyncio.run(execute(task))
    assert result['status']==expected
    assert result['started_at'] and result['finished_at'] and result['duration_seconds']>=0
    assert 'DEMO' in logs or simulation=='TIMEOUT'

def test_unknown_script_rejected():
    from worker.executor import execute
    result,logs=asyncio.run(execute({'script':'../../etc/passwd','timeout_seconds':2}))
    assert result['status']=='FAILURE'

def test_scheduler_due_only_and_future_expectations(database,monkeypatch):
    fixed=svc.datetime(2026,10,20,10,5,tzinfo=svc.UTC)
    monkeypatch.setattr(svc,'now',lambda:fixed)
    with db.transaction() as s:
        svc.expected(s,'2026-10-20','netbackup_jobs',host='nbu-emea-01')
        rs=svc.rows(s,{'date':'2026-10-20','host':'nbu-emea-01'})
        assert len([r for r in rs if r['status']=='MISSING'])==60
        assert len([r for r in rs if r['status']=='SCHEDULED'])==84
    svc.tick();svc.tick()
    with db.transaction() as s:
        # Windows read as MISSING remain deliberate backfills; the scheduler doesn't silently replay them.
        assert s.scalar(select(Window).where(Window.host_id=='nbu-emea-02',Window.collector_id=='netbackup_jobs',Window.period_start=='2026-10-20T09:50:00Z')).status=='PENDING'
        assert s.scalar(select(Window).where(Window.host_id=='nbu-emea-01',Window.collector_id=='netbackup_jobs',Window.period_start=='2026-10-20T10:00:00Z')).status=='SCHEDULED'
    monkeypatch.setattr(svc,'now',lambda:fixed+svc.timedelta(minutes=6))
    svc.tick()
    with db.transaction() as s:
        assert s.scalar(select(Window).where(Window.host_id=='nbu-emea-01',Window.collector_id=='netbackup_jobs',Window.period_start=='2026-10-20T10:00:00Z')).status=='PENDING'

def test_attempt_parameters_are_immutable_and_wrong_period_rejected(database):
    cid=one_window();task=svc.claim('EMEA-SAT-1')
    with db.transaction() as s:
        attempt=s.get(Attempt,task['task_id']);assert attempt.context['parameters']['simulation']=='SUCCESS'
        s.get(Collector,'netbackup_jobs').parameters={'simulation':'FAILED'}
    wrong={**payload(),'period_start':'2026-10-16T00:00:00Z'}
    with pytest.raises(ValueError,match='period'):svc.result(task['task_id'],'EMEA-SAT-1',wrong)
    svc.result(task['task_id'],'EMEA-SAT-1',payload())
    with db.transaction() as s:
        assert s.get(Attempt,task['task_id']).context['parameters']['simulation']=='SUCCESS'

def test_planning_after_cadence_change_reuses_all_original_windows(database):
    with db.transaction() as s:
        original=svc.expected(s,'2026-10-15','netbackup_jobs',host='nbu-emea-01')
        s.get(Collector,'netbackup_jobs').interval_seconds=3600
        planned=svc.expected(s,'2026-10-15','netbackup_jobs',host='nbu-emea-01',enqueue=True)
        assert set(planned)==set(original) and len(planned)==144
        assert all(s.get(Window,cid).status=='PENDING' for cid in original)
        assert all(s.get(Window,cid).snapshot['interval_seconds']==600 for cid in original)


def test_report_contract_and_unknown_retry(database):
    from orchestrator.schemas import CollectorResult
    report=CollectorResult(status='SUCCESS',summary='Delivered')
    assert report.collected_at.tzinfo and report.details=={}
    for invalid in [{'status':'OK'},{'status':'SUCCESS','data':{'jobs':[]}}, {'status':'SUCCESS','details':{'large':'x'*17000}}]:
        with pytest.raises(ValueError):CollectorResult.model_validate({'summary':'Report',**invalid})
    cid=one_window();task=svc.claim('EMEA-SAT-1')
    report={'status':'UNKNOWN','summary':'Delivery not confirmed','collected_at':'2026-10-20T10:00:00Z','details':{'reason':'DESTINATION_UNAVAILABLE'}}
    report.update(attempt_number=99,worker_id='wrong-worker')
    svc.result(task['task_id'],'EMEA-SAT-1',report,'Useful log line')
    with db.Session() as session:
        attempt=session.get(Attempt,task['task_id']);window=session.get(Window,cid)
        assert attempt.result['status']=='UNKNOWN' and attempt.status=='UNKNOWN'
        assert attempt.result['attempt_number']==1 and attempt.result['task_id']==task['task_id']
        assert attempt.result['host']=='nbu-emea-01' and attempt.result['worker_id']=='EMEA-SAT-1'
        assert attempt.result['started_at'] and attempt.result['finished_at'] and attempt.result['duration_seconds']>=0
        assert window.status=='PENDING' and attempt.logs=='Useful log line'
    from tests.client import DashboardClient
    from orchestrator.main import app
    with DashboardClient(app) as client:
        page=client.get('/collections/'+cid).text
        assert 'UNKNOWN' in page and 'Report details' in page and 'Useful log line' in page
