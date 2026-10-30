"""Read-only consistency audit for the currently configured Tasker database."""
import json
from collections import Counter,defaultdict
from sqlalchemy import select,text
from orchestrator import db,service as svc
from orchestrator.models import Host,Worker,Zone,Collector,Window,Attempt,HostGroup,Schedule

def audit():
    issues=[]
    with db.Session() as session:
        session.connection().exec_driver_sql("BEGIN")
        integrity=session.execute(text('PRAGMA integrity_check')).scalar()
        if integrity!='ok':issues.append('SQLite integrity: '+str(integrity))
        if session.execute(text('PRAGMA foreign_key_check')).all():issues.append('Foreign-key violations')
        hosts={h.id:h for h in session.scalars(select(Host))};zones={z.id:z for z in session.scalars(select(Zone))}
        workers={w.id:w for w in session.scalars(select(Worker))};collectors={c.id:c for c in session.scalars(select(Collector))}
        groups={g.id:g for g in session.scalars(select(HostGroup))};rules=list(session.scalars(select(Schedule)))
        for host in hosts.values():
            if host.zone not in zones:issues.append('Unknown zone on host '+host.id)
            if host.discovered and host.enabled:issues.append('Discovered host is enabled: '+host.id)
            if host.deleted and host.enabled:issues.append('Archived host is enabled: '+host.id)
            if any(wid and wid not in workers for wid in [host.preferred_worker,host.fallback_worker]):issues.append('Unknown satellite on host '+host.id)
        for host in hosts.values():
            for wid in (host.preferred_worker,host.fallback_worker):
                if wid and wid in workers and (workers[wid].zones!=[host.zone] or host.scope not in workers[wid].scopes):issues.append('Host/satellite routing mismatch: '+host.id)
        for worker in workers.values():
            if len(worker.zones)!=1:issues.append('Satellite needs exactly one zone: '+worker.id)
            if any(z not in zones for z in worker.zones):issues.append('Unknown zone on satellite '+worker.id)
        for collector in collectors.values():
            if collector.zone and collector.zone not in zones:issues.append('Unknown collector workspace: '+collector.id)
        for group in groups.values():
            if group.zone and any(hid in hosts and hosts[hid].zone!=group.zone for hid in group.host_ids):issues.append('Group crosses zone workspaces: '+group.id)
            if len(group.host_ids)!=len(set(group.host_ids)):issues.append('Duplicate group members: '+group.id)
            if any(hid not in hosts for hid in group.host_ids):issues.append('Unknown group member: '+group.id)
        invalid_rules=False
        for rule in rules:
            if rule.zone and rule.collector_id in collectors and collectors[rule.collector_id].zone!=rule.zone:issues.append("Schedule/collector workspace mismatch: "+rule.id)
            if rule.collector_id not in collectors or any(gid not in groups for gid in svc.rule_group_ids(rule)):
                issues.append('Unknown schedule target or collector: '+rule.id);invalid_rules=True
        try:
            if not invalid_rules:svc.validate_groups_and_schedules(session,require_targets=False)
        except ValueError as error:issues.append(str(error))
        windows=list(session.scalars(select(Window).order_by(Window.host_id,Window.collector_id,Window.period_start)))
        window_ids={w.id for w in windows}
        attempts=defaultdict(list)
        for attempt in session.scalars(select(Attempt).order_by(Attempt.number)):
            attempts[attempt.window_id].append(attempt)
            if attempt.window_id not in window_ids:issues.append('Unknown attempt window: '+attempt.id)
            if attempt.status=='RUNNING' and attempt.finished_at is not None:issues.append('Running attempt has finished timestamp: '+attempt.id)
        last={};seen=set()
        for window in windows:
            key=(window.host_id,window.collector_id);unique=(*key,window.period_start)
            if unique in seen:issues.append('Duplicate window: '+window.id)
            seen.add(unique)
            start,end=svc.dt(window.period_start),svc.dt(window.period_end)
            if end<=start:issues.append('Invalid period: '+window.id)
            if window.snapshot.get('schedule_kind')!='daily_local' and (end-start).total_seconds()!=window.snapshot.get('interval_seconds'):issues.append('Snapshot duration mismatch: '+window.id)
            if key in last and start<last[key]:issues.append('Overlapping periods: '+window.id)
            last[key]=max(last.get(key,end),end)
            history=attempts[window.id]
            if len({a.number for a in history})!=len(history):issues.append('Duplicate attempt number: '+window.id)
            if window.status=='SUCCESS' and (not history or history[-1].status!='SUCCESS'):issues.append('Success without successful last attempt: '+window.id)
            if window.status=='RUNNING' and sum(a.status=='RUNNING' for a in history)!=1:issues.append('Running window/attempt mismatch: '+window.id)
            if window.status!='RUNNING' and any(a.status=='RUNNING' for a in history):issues.append('Unattached running attempt: '+window.id)
        return {'ok':not issues,'issues':issues[:50],'issue_count':len(issues),'counts':{'zones':len(zones),'hosts':len(hosts),'satellites':len(workers),'collectors':len(collectors),'host_groups':len(groups),'schedules':len(rules),'windows':len(windows),'attempts':sum(map(len,attempts.values()))},'window_states':dict(Counter(w.status for w in windows))}

if __name__=='__main__':
    result=audit();print(json.dumps(result,indent=2));raise SystemExit(0 if result['ok'] else 1)
