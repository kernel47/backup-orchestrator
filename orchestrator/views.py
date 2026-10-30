"""Read-only health summaries for the active zone workspace."""
from collections import Counter
from datetime import datetime,timezone,timedelta


def latest_collector_health(session):
    """One latest recorded period per host, across each satellite's local clock."""
    from sqlalchemy import select,func
    from .models import Window,Attempt,Collector,Schedule,Host
    from . import service as svc
    ranked=select(Window.id,func.row_number().over(partition_by=(Window.collector_id,Window.host_id),order_by=(Window.period_start.desc(),Window.created_at.desc())).label('position')).where(Window.status.notin_(['SCHEDULED','DISABLED'])).subquery()
    latest=list(session.scalars(select(Window).join(ranked,Window.id==ranked.c.id).where(ranked.c.position==1)))
    by_collector={}
    for window in latest:by_collector.setdefault(window.collector_id,{})[window.host_id]=window
    successes=dict(session.execute(select(Window.collector_id,func.max(Attempt.finished_at)).join(Attempt,Attempt.window_id==Window.id).where(Attempt.status=='SUCCESS').group_by(Window.collector_id)).all())
    result={}
    for collector in session.scalars(select(Collector)):
        recorded=by_collector.get(collector.id,{})
        rules=list(session.scalars(select(Schedule).where(Schedule.collector_id==collector.id,Schedule.enabled.is_(True))))
        targets={h.id for rule in rules for h in svc.rule_targets(session,rule)}
        if collector.legacy_schedule and collector.enabled:
            targets.update(h.id for h in session.scalars(select(Host).where(Host.scope==collector.scope)) if svc.host_active(session,h))
        # With no active schedule, show manual/paused execution history explicitly.
        ids=targets if rules or collector.legacy_schedule and collector.enabled else set(recorded)
        counts=Counter(recorded[hid].status if hid in recorded else 'UNRECORDED' for hid in ids)
        failures=counts['FAILED']+counts['TIMEOUT']+counts['UNKNOWN']
        status='FAILED' if failures else 'MISSING' if counts['MISSING'] else 'RUNNING' if counts['RUNNING']+counts['PENDING'] else 'SUCCESS' if counts['SUCCESS'] and counts['SUCCESS']==len(ids) else 'DISABLED'
        stale=sum(1 for hid in targets if hid in recorded and recorded[hid].status not in ('RUNNING','PENDING') and svc.now()>svc.dt(recorded[hid].period_end)+timedelta(seconds=recorded[hid].snapshot.get('interval_seconds',collector.interval_seconds)+300)) if rules or collector.legacy_schedule and collector.enabled else 0
        if stale and status=='SUCCESS':status='MISSING'
        last=successes.get(collector.id)
        result[collector.id]={'counts':counts,'expected':len(ids),'last_period':max((w.period_start[:10] for w in recorded.values()),default=''),'scheduled':bool(rules or collector.legacy_schedule and collector.enabled),'status':status,'stale':stale,'last_success':svc.iso(datetime.fromtimestamp(last,timezone.utc)) if last else None}
    return result


def overview_activity(session):
    """Seven days of persisted periods and a bounded feed of execution attempts."""
    from sqlalchemy import select,func
    from .models import Window,Attempt,Collector
    from . import service as svc
    today=svc.now().replace(hour=0,minute=0,second=0,microsecond=0)
    days=[(today-timedelta(days=n)).strftime('%Y-%m-%d') for n in range(6,-1,-1)]
    grouped={day:Counter() for day in days}
    for day,status,count in session.execute(select(func.substr(Window.period_start,1,10),Window.status,func.count()).where(Window.period_start>=days[0]+'T00:00:00Z',Window.period_start<svc.iso(today+timedelta(days=1))).group_by(func.substr(Window.period_start,1,10),Window.status)):
        grouped[day][status]=count
    trend=[{'day':day,'counts':counts,'total':sum(v for k,v in counts.items() if k not in ('SCHEDULED','DISABLED'))} for day,counts in grouped.items()]
    maximum=max((d['total'] for d in trend),default=0) or 1
    recent=[]
    # Select only summary fields: do not load historical logs or report datasets.
    for attempt,window_id,host,collector,status,started,finished in session.execute(select(Attempt.id,Window.id,Window.host_id,Collector.name,Attempt.status,Attempt.started_at,Attempt.finished_at).join(Window,Attempt.window_id==Window.id).join(Collector,Window.collector_id==Collector.id).order_by(Attempt.started_at.desc(),Attempt.id).limit(6)):
        recent.append({'id':attempt,'window_id':window_id,'host':host,'collector':collector,'status':status,'time':svc.iso(datetime.fromtimestamp(finished or started,timezone.utc))})
    return {'trend':trend,'trend_max':maximum,'recent':recent}
