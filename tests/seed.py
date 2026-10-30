"""Test-only history fixtures. Never used by installation or normal startup."""
from datetime import timedelta
from sqlalchemy import select
from orchestrator import db,service as svc
from orchestrator.models import Window,Collector,Attempt

def seed():
    db.init()
    from tests.support import populate
    populate(include_schedules=True)
    with db.transaction() as s:
        if s.scalar(select(Window).limit(1)):return
        current=svc.now();today=current.strftime('%Y-%m-%d')
        days=sorted({(current-timedelta(days=n)).strftime('%Y-%m-%d') for n in range(3)}|{'2026-10-15'})
        for day in days:svc.expected(s,day)
        for w in s.scalars(select(Window)):
            if w.status in ('SCHEDULED','DISABLED'):continue
            c=s.get(Collector,w.collector_id)
            failure_start=svc.dt(w.period_start[:10]+'T10:00:00Z')
            if w.period_start.startswith(today):
                latest=svc.datetime.fromtimestamp(int(current.timestamp())//600*600,svc.UTC)-timedelta(minutes=10)
                failure_start=min(failure_start,latest)
            missing_start=failure_start-timedelta(minutes=10)
            if c.name=='Jobs' and w.host_id=='nbu-emea-01' and svc.dt(w.period_start)==missing_start:
                continue
            failed=c.name=='Jobs' and w.host_id in ['nbu-emea-02','nbu-emea-03','nbu-emea-04'] and svc.dt(w.period_start)==failure_start
            w.status='FAILED' if failed else 'SUCCESS'
            stamp=svc.dt(w.period_end).timestamp()
            result={'status':'FAILURE' if failed else 'SUCCESS','summary':'Test historical report',
                    'collected_at':svc.iso(svc.dt(w.period_end)+timedelta(seconds=2)),
                    'details':{'simulated':True,'records_collected':0 if failed else 125}}
            h=s.get(svc.Host,w.host_id)
            s.add(Attempt(id=svc.uid(),window_id=w.id,worker_id=h.preferred_worker,number=1,status=w.status,
                          started_at=stamp+1,finished_at=stamp+2,lease_until=0,result=result,
                          logs='DEMO fixture — this historical result was not produced by a script execution.',context=dict(w.snapshot,source='demo_fixture')))
    print('Demo inventory and history initialized.')
if __name__=='__main__':seed()
