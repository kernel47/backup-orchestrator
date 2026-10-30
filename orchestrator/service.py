import time,uuid
from bisect import bisect_left
from datetime import datetime,timedelta,timezone
from collections import defaultdict
from sqlalchemy import select
from .models import Worker,Host,Collector,Window,Attempt,HostGroup,Schedule,Zone
from .db import transaction
from .config import settings
from .scheduling import latest_daily_period,daily_period,next_daily,local_label
UTC=timezone.utc
def uid():return str(uuid.uuid4())
def now():return datetime.now(UTC)
def iso(value):return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00","Z")
def dt(value):
    result=datetime.fromisoformat(value.replace("Z","+00:00"))
    if result.tzinfo is None:raise ValueError("UTC timezone required")
    return result.astimezone(UTC)
def dump(obj):
    data={col.name:getattr(obj,col.name) for col in obj.__table__.columns if col.name not in ("token_hash","token_env")}
    if isinstance(obj,Worker):data['zone']=obj.zones[0] if len(obj.zones)==1 else None
    return data
def bootstrap():
    with transaction() as s:
        for worker in s.scalars(select(Worker)):
            if len(worker.zones)!=1:raise ValueError('Satellite '+worker.id+' must belong to exactly one zone; fix its assignment before startup')
        # Upgrade existing installations without importing configuration on fresh masters.
        ids={h.zone for h in s.scalars(select(Host))}|{z for w in s.scalars(select(Worker)) for z in w.zones}
        for zid in ids:
            if zid and not s.get(Zone,zid):s.add(Zone(id=zid,name=zid,enabled=True))

def host_active(s,h):
    zone=s.get(Zone,h.zone)
    return h.enabled and not h.deleted and not h.discovered and zone and zone.enabled

def origin_groups_active(s,w):
    ids=w.snapshot.get("host_group_ids") or ([w.snapshot["host_group_id"]] if w.snapshot.get("host_group_id") else [])
    return not ids or any((g:=s.get(HostGroup,gid)) and g.enabled and not g.deleted for gid in ids)

def rule_group_ids(rule):return rule.host_group_ids or [rule.host_group_id]

def resolve_targets(s,collector_id,group_ids,zones=None,allow_inactive=False):
    c=s.get(Collector,collector_id)
    if not c:raise ValueError("Unknown collector")
    if not group_ids:raise ValueError("Choose at least one host group")
    if any(not s.get(Zone,z) for z in (zones or [])):raise ValueError("Unknown target zone")
    targets=[];skipped=[];seen=set()
    for gid in dict.fromkeys(group_ids):
        g=s.get(HostGroup,gid)
        if not g or g.deleted:raise ValueError("Unknown host group")
        if c.zone and g.zone and c.zone!=g.zone:raise ValueError("Collector and host group must belong to the same zone")
        if not g.enabled and not allow_inactive:raise ValueError("Host group is disabled")
        for hid in g.host_ids:
            if hid in seen:continue
            seen.add(hid);h=s.get(Host,hid)
            reason="Unknown host" if not h else "Host deleted" if h.deleted else "Host disabled" if not h.enabled or h.discovered else "Zone inactive" if not host_active(s,h) else "Outside selected zones" if zones and h.zone not in zones else "Scope mismatch" if h.scope!=c.scope else "Zone mismatch" if c.zone and h.zone!=c.zone else ""
            if reason:skipped.append({"host":hid,"reason":reason})
            else:targets.append(h)
    return c,targets,skipped

def rule_targets(s,rule):
    groups=[s.get(HostGroup,g) for g in rule_group_ids(rule)]
    if any(not g or g.deleted or not g.enabled for g in groups):return []
    return resolve_targets(s,rule.collector_id,rule_group_ids(rule),rule.zones)[1]

def clock_worker(s,h):
    """Keep the preferred satellite's clock stable even when it is offline."""
    candidates=[s.get(Worker,wid) for wid in [h.preferred_worker,h.fallback_worker] if wid]
    if not h.preferred_worker and not h.fallback_worker:candidates=list(s.scalars(select(Worker).order_by(Worker.id)))
    for worker in candidates:
        if worker and worker.zones==[h.zone] and h.scope in worker.scopes:
            # An unknown preferred clock must not silently adopt a different one.
            return worker if worker.timezone else None
    return None

def schedule_timing(s,rule):
    result={};current=now()
    for host in rule_targets(s,rule):
        worker=clock_worker(s,host)
        if not worker:continue
        if worker.id in result:continue
        due=next_daily(current,rule.local_time,worker.timezone) if rule.kind=="daily_local" else datetime.fromtimestamp((int(current.timestamp())//rule.interval_seconds+1)*rule.interval_seconds,UTC)
        result[worker.id]={"worker_id":worker.id,"timezone":worker.timezone,"local_time":local_label(current,worker.timezone),"next_execution":iso(due),"next_execution_local":local_label(due,worker.timezone)}
    return list(result.values())

def rule_periods(s,h,rule,start,end,interval):
    if rule and rule.kind=="daily_local":
        worker=clock_worker(s,h)
        if not worker:return
        day=start.date()-timedelta(days=2)
        while day<=end.date()+timedelta(days=2):
            p,q=daily_period(day,rule.local_time,worker.timezone)
            if start<=p<end:yield p,q
            day+=timedelta(days=1)
    else:
        p=start
        while p<end:
            yield p,p+timedelta(seconds=interval)
            p+=timedelta(seconds=interval)

def bounds(day):
    start=datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=UTC)
    return start,start+timedelta(days=1)
def snapshot(c,h,parameters=None):
    return {"script":c.script,"interval_seconds":c.interval_seconds,"timeout_seconds":c.timeout_seconds,
            "max_retries":c.max_retries,"revision":c.revision,"zone":h.zone,"scope":h.scope,
            "parameters":{**h.parameters,**c.parameters,**(parameters or {})}}
def expected(s,day,collector_id=None,host="",enqueue=False,parameters=None,zone=""):
    start,end=bounds(day);ids=[];current=now()
    known={(w.host_id,w.collector_id,w.period_start):w for w in s.scalars(select(Window).where(Window.period_start>=iso(start),Window.period_start<iso(end)))}
    for c in s.scalars(select(Collector)):
        if collector_id and c.id!=collector_id:continue
        for h in s.scalars(select(Host).where(Host.scope==c.scope)):
            if zone and h.zone!=zone or host and h.id!=host:continue
            rule=host_rule(s,c,h)
            interval=rule.interval_seconds if rule else c.interval_seconds
            # Reuse every historical window, even if the current cadence has changed.
            for existing in list(known.values()):
                if existing.host_id==h.id and existing.collector_id==c.id:
                    ids.append(existing.id)
                    if enqueue and c.enabled and h.enabled and dt(existing.period_end)<=current and existing.status in ("MISSING","FAILED","TIMEOUT"):
                        queue(s,existing,parameters)
            if rule is False or h.deleted or h.discovered:continue
            intervals=sorted((w.period_start,w.period_end) for w in known.values() if w.host_id==h.id and w.collector_id==c.id)
            starts=[a for a,b in intervals]
            for p,q in rule_periods(s,h,rule,start,end,interval):
                if rule and rule.kind=="daily_local" and q.timestamp()<rule.created_at and not enqueue:continue
                key=(h.id,c.id,iso(p));w=known.get(key)
                # Preserve historical windows when cadence changes; never create overlapping windows.
                insertion=bisect_left(starts,iso(q))
                overlap=not w and insertion>0 and intervals[insertion-1][1]>iso(p)
                if not w and not overlap:
                    status="DISABLED" if not c.enabled or not host_active(s,h) else "SCHEDULED" if q>current else "PENDING" if enqueue else "MISSING"
                    w=Window(id=uid(),host_id=h.id,collector_id=c.id,period_start=iso(p),period_end=iso(q),status=status,created_at=time.time(),available_at=0,retry_base=0,snapshot=dict(execution_snapshot(c,h,rule,parameters,session=s),scheduled_for=iso(q),manual_trigger=enqueue));s.add(w);known[key]=w
                    index=bisect_left(starts,w.period_start)
                    starts.insert(index,w.period_start);intervals.insert(index,(w.period_start,w.period_end))
                if w:
                    if enqueue and c.enabled and h.enabled and dt(w.period_end)<=current and w.status in ("MISSING","FAILED","TIMEOUT"):
                        queue(s,w,parameters)
                    ids.append(w.id)
    s.flush();return list(dict.fromkeys(ids))

def queue(s,w,parameters=None,manual=True):
    c=s.get(Collector,w.collector_id);h=s.get(Host,w.host_id)
    if not c.enabled or not host_active(s,h) or h.scope!=c.scope or c.zone and c.zone!=h.zone or not origin_groups_active(s,w):return False
    count=len(list(s.scalars(select(Attempt).where(Attempt.window_id==w.id))))
    w.retry_base=count;w.status="PENDING";w.available_at=0
    # Exact historical window/cadence stays fixed; execution settings may be updated explicitly.
    old_interval=w.snapshot["interval_seconds"]
    old_group=w.snapshot.get("host_group_id")
    original_clock={k:w.snapshot[k] for k in ("schedule_kind","local_time","execution_timezone","clock_worker_id","scheduled_for") if k in w.snapshot}
    rule=s.get(Schedule,w.snapshot["schedule_id"]) if w.snapshot.get("schedule_id") else None
    w.snapshot=dict(execution_snapshot(c,h,rule,parameters,session=s),interval_seconds=old_interval)
    w.snapshot=dict(w.snapshot,**original_clock,manual_trigger=manual)
    if old_group:w.snapshot=dict(w.snapshot,host_group_id=old_group)
    return True

def rows(s,filters=None,include_attempts=True,window_ids=None):
    filters=filters or {};query=select(Window)
    if window_ids is not None:query=query.where(Window.id.in_(window_ids))
    if filters.get("date"):
        a,b=bounds(filters["date"]);query=query.where(Window.period_start>=iso(a),Window.period_start<iso(b))
    if filters.get("start"):query=query.where(Window.period_start>=filters["start"])
    if filters.get("end"):query=query.where(Window.period_start<filters["end"])
    if filters.get("collector_id"):query=query.where(Window.collector_id==filters["collector_id"])
    hs={h.id:h for h in s.scalars(select(Host))};cs={c.id:c for c in s.scalars(select(Collector))};ats=defaultdict(list)
    windows=list(s.scalars(query.order_by(Window.period_start,Window.host_id)))
    ids=[w.id for w in windows]
    failed_attempts=set()
    if ids and include_attempts:
        for a in s.scalars(select(Attempt).where(Attempt.window_id.in_(ids)).order_by(Attempt.number)):ats[a.window_id].append(dump(a))
    elif ids:
        # Aggregate report metadata in SQL; do not load log/result bodies for every period.
        failed_attempts=set(s.scalars(select(Attempt.window_id).join(Window,Window.id==Attempt.window_id).where(Attempt.window_id.in_(query.with_only_columns(Window.id).order_by(None)),Attempt.status.notin_(['SUCCESS','RUNNING'])).distinct()))
    out=[]
    for w in windows:
        h=hs[w.host_id];c=cs[w.collector_id];attempts=ats[w.id]
        r=dict(dump(w),host=h.id,zone=w.snapshot.get("zone",h.zone),scope=w.snapshot.get("scope",c.scope),collector=c.name,attempts=attempts,
               worker=attempts[-1]["worker_id"] if attempts else None,summary=(attempts[-1]["result"] or {}).get("summary","") if attempts else "")
        if filters.get("host_group_id") and filters["host_group_id"] not in w.snapshot.get("host_group_ids",[w.snapshot.get("host_group_id")]):continue
        if filters.get("schedule_id") and w.snapshot.get("schedule_id")!=filters["schedule_id"]:continue
        if any(filters.get(k) and r[k]!=filters[k] for k in ("zone","scope","host","worker")):continue
        if filters.get("status")=="RECOVERED":
            if not (r["status"]=="SUCCESS" and (w.id in failed_attempts or any(a["status"] not in ("SUCCESS","RUNNING") for a in attempts))):continue
        elif filters.get("status")=="ATTENTION":
            if r['status'] not in ('FAILED','TIMEOUT','MISSING','UNKNOWN'):continue
        elif filters.get("status") and r['status']!=filters['status']:continue
        if filters.get("start") and dt(w.period_start)<dt(filters["start"]):continue
        if filters.get("end") and dt(w.period_start)>=dt(filters["end"]):continue
        planned_zone=w.snapshot.get("execution_timezone","")
        if not planned_zone and not attempts and w.snapshot.get("schedule_id"):
            clock=clock_worker(s,h)
            if clock:planned_zone=clock.timezone
        actual_zone=attempts[-1].get("context",{}).get("worker_timezone","") if attempts else ""
        r['recovered']=w.status=='SUCCESS' and (w.id in failed_attempts or any(a['status'] not in ('SUCCESS','RUNNING') for a in attempts))
        r.update(execution_timezone=planned_zone,scheduled_utc=w.snapshot.get("scheduled_for",w.period_end),scheduled_local=local_label(w.snapshot.get("scheduled_for",w.period_end),planned_zone) if planned_zone else "",started_local=local_label(attempts[-1]["started_at"],actual_zone) if attempts and actual_zone else "",actual_timezone=actual_zone)
        out.append(r)
    return out

def retry(filters,ids=None,mode="retry"):
    with transaction() as s:
        from fastapi import HTTPException
        if ids is not None and any(not s.get(Window,wid) for wid in ids):raise HTTPException(404,'Collection not found in this workspace')
        selected=rows(s,filters);queued=[]
        for row in selected:
            if ids is not None and row["id"] not in ids:continue
            w=s.get(Window,row["id"])
            allowed=('SUCCESS',) if mode=='rerun' else ('FAILED','TIMEOUT','MISSING','UNKNOWN')
            if w.status in allowed and queue(s,w):queued.append(w.id)
        return queued

def report_metadata(a,w,payload):
    """Bind report identity to the assigned attempt, including replayed submissions."""
    payload=dict(payload)
    worker_dates=bool(payload.get('started_at') and payload.get('finished_at'))
    payload.update(attempt_number=a.number,task_id=a.id,worker_id=a.worker_id,host=w.host_id,collector_id=w.collector_id)
    if a.status!='RUNNING' and a.result:
        for field in ('started_at','finished_at','duration_seconds'):
            if payload.get(field) is None:payload[field]=a.result.get(field)
    payload.setdefault('started_at',iso(datetime.fromtimestamp(a.started_at,UTC)))
    if not payload.get('started_at'):payload['started_at']=iso(datetime.fromtimestamp(a.started_at,UTC))
    if not payload.get('finished_at'):payload['finished_at']=iso(datetime.fromtimestamp(a.finished_at or time.time(),UTC))
    if payload.get('duration_seconds') is None:payload['duration_seconds']=round(max(0,(dt(payload['finished_at'])-dt(payload['started_at'])).total_seconds() if worker_dates else (a.finished_at or time.time())-a.started_at),3)
    payload.setdefault('records_collected',0);payload.setdefault('records_sent',0);payload.setdefault('error',None)
    return payload

def fail(s,a,status,error):
    w=s.get(Window,a.window_id);a.status=status;a.finished_at=time.time()
    a.result=report_metadata(a,w,{"status":"TIMEOUT" if status=="TIMEOUT" else "UNKNOWN","summary":error,"error":error,"collected_at":iso(now()),"details":{"reason":status}})
    if a.number-w.retry_base<=w.snapshot["max_retries"]:
        w.status="PENDING";w.available_at=time.time()+min(60,2**(a.number-w.retry_base))
    else:w.status="TIMEOUT" if status=="TIMEOUT" else "UNKNOWN" if status in ("UNKNOWN","WORKER_OFFLINE") else "FAILED"

def recover(s):
    timestamp=time.time()
    for a in s.scalars(select(Attempt).where(Attempt.status=="RUNNING")):
        w=s.get(Window,a.window_id);worker=s.get(Worker,a.worker_id)
        if timestamp-a.started_at>w.snapshot["timeout_seconds"] or timestamp>a.lease_until:
            fail(s,a,"TIMEOUT","Collection timeout or lease expired")
        elif not worker or timestamp-worker.heartbeat>settings.offline_seconds:
            fail(s,a,"WORKER_OFFLINE","Satellite went offline during collection")

def claim(worker_id):
    with transaction() as s:
        recover(s);worker=s.get(Worker,worker_id)
        if not worker:raise ValueError("Unknown satellite")
        if len(worker.zones)!=1:return None
        worker.heartbeat=time.time()
        active=list(s.scalars(select(Attempt).where(Attempt.worker_id==worker_id,Attempt.status=="RUNNING")))
        if len(active)>=worker.concurrency:return None
        for w in s.scalars(select(Window).where(Window.status=="PENDING",Window.available_at<=time.time()).order_by(Window.period_start)):
            h=s.get(Host,w.host_id);c=s.get(Collector,w.collector_id)
            if not host_active(s,h) or not c.enabled or h.scope!=c.scope or c.zone and c.zone!=h.zone or not origin_groups_active(s,w):w.status="DISABLED";continue
            if h.scope not in worker.scopes or worker.zones!=[h.zone]:continue
            if w.snapshot.get("schedule_kind")=="daily_local" and not w.snapshot.get("manual_trigger") and worker.timezone!=w.snapshot.get("execution_timezone"):continue
            preferred=s.get(Worker,h.preferred_worker)
            preferred_eligible=preferred and h.scope in preferred.scopes and preferred.zones==[h.zone]
            if h.preferred_worker and worker.id!=h.preferred_worker and not (worker.id==h.fallback_worker and (not preferred_eligible or time.time()-preferred.heartbeat>settings.offline_seconds)):continue
            n=len(list(s.scalars(select(Attempt).where(Attempt.window_id==w.id))))+1
            a=Attempt(id=uid(),window_id=w.id,worker_id=worker.id,number=n,status="RUNNING",started_at=time.time(),lease_until=time.time()+settings.lease_seconds,logs="",context=dict(w.snapshot,worker_timezone=worker.timezone,scheduled_for=w.snapshot.get("scheduled_for",w.period_end),period_start=w.period_start,period_end=w.period_end,collector_id=w.collector_id,host=h.id,zone=h.zone,scope=h.scope))
            s.add(a);w.status="RUNNING";s.flush()
            return {"task_id":a.id,"collection_id":w.id,"collector_id":c.id,"collector":c.name,"host":h.id,"zone":h.zone,"scope":h.scope,"period_start":w.period_start,"period_end":w.period_end,**w.snapshot,"zone":h.zone,"scope":h.scope,"lease_seconds":settings.lease_seconds,"worker_timezone":worker.timezone,"scheduled_for":w.snapshot.get("scheduled_for",w.period_end),"attempt_number":a.number,"worker_id":worker.id}
    return None

def result(task_id,worker_id,payload,logs=""):
    with transaction() as s:
        a=s.get(Attempt,task_id)
        if not a or a.worker_id!=worker_id:raise ValueError("Unknown attempt")
        w=s.get(Window,a.window_id)
        payload=dict(payload)
        for field in ("period_start","period_end"):
            expected_period=getattr(w,field)
            if payload.get(field) and dt(payload[field])!=dt(expected_period):raise ValueError("Result period does not match task")
            payload[field]=expected_period
        payload=report_metadata(a,w,payload)
        if a.status!="RUNNING":
            if a.result==payload:return {"idempotent":True}
            raise ValueError("Stale or conflicting result")
        w=s.get(Window,a.window_id)
        if time.time()>a.lease_until or time.time()-a.started_at>w.snapshot["timeout_seconds"]:raise ValueError("Attempt expired")
        a.logs=logs[-20000:]
        if payload["status"]=="SUCCESS":a.status="SUCCESS";a.finished_at=time.time();w.status="SUCCESS"
        else:fail(s,a,"TIMEOUT" if payload["status"]=="TIMEOUT" else "UNKNOWN" if payload["status"]=="UNKNOWN" else "FAILED",payload["summary"])
        a.result=payload
    return {"accepted":True}

def tick():
    current=now()
    with transaction() as s:
        recover(s)
        for w in s.scalars(select(Window).where(Window.status=="SCHEDULED")):
            if dt(w.period_end)<=current:
                h=s.get(Host,w.host_id);c=s.get(Collector,w.collector_id)
                rule=s.get(Schedule,w.snapshot["schedule_id"]) if w.snapshot.get("schedule_id") else None
                allowed=not w.snapshot.get("schedule_id") and c.legacy_schedule or rule and rule.enabled and any(target.id==h.id for target in rule_targets(s,rule))
                w.status="PENDING" if host_active(s,h) and c.enabled and allowed else "DISABLED"
        # Only the most recent closed window is scheduled on restart. Gaps remain MISSING until requested.
        for c in s.scalars(select(Collector).where(Collector.enabled==True,Collector.legacy_schedule==True)):
            end=datetime.fromtimestamp(int(current.timestamp())//c.interval_seconds*c.interval_seconds,UTC)
            start=end-timedelta(seconds=c.interval_seconds)
            for h in s.scalars(select(Host).where(Host.enabled==True,Host.deleted==False,Host.discovered==False,Host.scope==c.scope)):
                if not host_active(s,h):continue
                w=s.scalar(select(Window).where(Window.host_id==h.id,Window.collector_id==c.id,Window.period_start==iso(start)))
                if not w:
                    overlap=s.scalar(select(Window).where(Window.host_id==h.id,Window.collector_id==c.id,Window.period_start<iso(end),Window.period_end>iso(start)))
                    if not overlap:s.add(Window(id=uid(),host_id=h.id,collector_id=c.id,period_start=iso(start),period_end=iso(end),status="PENDING",available_at=0,retry_base=0,created_at=time.time(),snapshot=snapshot(c,h)))

        for rule in s.scalars(select(Schedule).where(Schedule.enabled==True)):
            c=s.get(Collector,rule.collector_id);targets=rule_targets(s,rule)
            if not c.enabled:continue
            for h in targets:
                w=closed_window(s,c,h,rule.interval_seconds,rule=rule)
                if w and w.status in ("MISSING","SCHEDULED","DISABLED"):
                    w.snapshot=dict(execution_snapshot(c,h,rule,session=s),scheduled_for=w.period_end);queue(s,w,manual=False)

# Explicit inventory groups and collector-to-group schedules.
def group_targets(s,collector_id,group_id):
    c,targets,skipped=resolve_targets(s,collector_id,[group_id])
    return c,s.get(HostGroup,group_id),targets,skipped

def validate_groups_and_schedules(s,require_targets=True):
    schedules=list(s.scalars(select(Schedule).where(Schedule.enabled==True)))
    assigned={}
    for rule in schedules:
        groups=[s.get(HostGroup,gid) for gid in rule_group_ids(rule)]
        if any(not g or not g.enabled or g.deleted for g in groups):continue
        c=s.get(Collector,rule.collector_id);targets=rule_targets(s,rule)
        if require_targets and not targets:raise ValueError("An enabled schedule needs at least one active compatible host")
        members={hid for g in groups for hid in g.host_ids}
        for hid in members:
            h=s.get(Host,hid)
            if not h or h.deleted or h.scope!=c.scope or rule.zones and h.zone not in rule.zones:continue
            key=(c.id,h.id)
            if key in assigned:raise ValueError(f"Host {h.id} already has an enabled schedule for collector {c.name}: {assigned[key]}")
            assigned[key]=rule.name

def host_rule(s,c,h):
    if c.zone and c.zone!=h.zone:return False
    if c.legacy_schedule:return None
    for rule in s.scalars(select(Schedule).where(Schedule.collector_id==c.id,Schedule.enabled==True)):
        if any(target.id==h.id for target in rule_targets(s,rule)):return rule
    return False

def execution_snapshot(c,h,rule=None,parameters=None,session=None):
    data=snapshot(c,h,{**(rule.parameters if rule else {}),**(parameters or {})})
    if rule:
        matching=[gid for gid in rule_group_ids(rule) if session and h.id in session.get(HostGroup,gid).host_ids] or rule_group_ids(rule)
        data.update(interval_seconds=rule.interval_seconds,schedule_kind=rule.kind,local_time=rule.local_time,schedule_id=rule.id,host_group_id=(matching or rule_group_ids(rule))[0],host_group_ids=matching or rule_group_ids(rule))
        worker=clock_worker(session,h) if session else None
        if worker:data.update(execution_timezone=worker.timezone,clock_worker_id=worker.id)
    return data

def closed_window(s,c,h,interval,parameters=None,rule=None,manual=False):
    if rule and rule.kind=="daily_local":
        worker=clock_worker(s,h)
        if not worker:return None
        start,end=latest_daily_period(now(),rule.local_time,worker.timezone)
        if end.timestamp()<rule.created_at and not manual:return None
    else:
        end=datetime.fromtimestamp(int(now().timestamp())//interval*interval,UTC)
        start=end-timedelta(seconds=interval)
    w=s.scalar(select(Window).where(Window.host_id==h.id,Window.collector_id==c.id,Window.period_start==iso(start),Window.period_end==iso(end)))
    if w:return w
    overlap=s.scalar(select(Window).where(Window.host_id==h.id,Window.collector_id==c.id,Window.period_start<iso(end),Window.period_end>iso(start)))
    if overlap:return None
    w=Window(id=uid(),host_id=h.id,collector_id=c.id,period_start=iso(start),period_end=iso(end),status="PENDING",available_at=0,retry_base=0,created_at=time.time(),snapshot=dict(execution_snapshot(c,h,rule,parameters,session=s),scheduled_for=iso(end),manual_trigger=manual))
    s.add(w);s.flush();return w

def run_group(collector_id,group_id,parameters=None,group_ids=None,zones=None):
    with transaction() as s:
        gids=list(dict.fromkeys(group_ids or [group_id]))
        c,targets,skipped=resolve_targets(s,collector_id,gids,zones)
        if not c.enabled:raise ValueError("Collector is paused")
        if not targets:raise ValueError("No enabled hosts match this collector scope")
        ids=[]
        rule=next((r for r in s.scalars(select(Schedule).where(Schedule.collector_id==c.id,Schedule.enabled==True)) if set(rule_group_ids(r))==set(gids) and set(r.zones)==set(zones or [])),None)
        interval=rule.interval_seconds if rule else c.interval_seconds
        for h in targets:
            w=closed_window(s,c,h,interval,parameters,rule,manual=True)
            if not w:
                skipped.append({"host":h.id,"reason":"An existing historical window overlaps this period"});continue
            if w.status in ("MISSING","FAILED","TIMEOUT"):
                w.snapshot=execution_snapshot(c,h,rule,parameters,session=s)
                queue(s,w,parameters)
                w.snapshot=dict(w.snapshot,host_group_id=next(gid for gid in gids if h.id in s.get(HostGroup,gid).host_ids),host_group_ids=[gid for gid in gids if h.id in s.get(HostGroup,gid).host_ids])
            if w.status=="PENDING" and not w.snapshot.get("host_group_id") and not list(s.scalars(select(Attempt).where(Attempt.window_id==w.id))):
                w.snapshot=dict(w.snapshot,host_group_id=next(gid for gid in gids if h.id in s.get(HostGroup,gid).host_ids),host_group_ids=[gid for gid in gids if h.id in s.get(HostGroup,gid).host_ids])
            ids.append(w.id)
        return {"ids":ids,"skipped":skipped,"period_seconds":interval}
