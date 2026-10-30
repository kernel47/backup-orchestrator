"""Managed inventory: normalize, preview a diff, then atomically apply it."""
import hashlib,json,time
from pathlib import Path
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select
from . import db,service as svc
from .config import ROOT
from .workspaces import current_zone
from .models import Host,Worker,Zone,InventorySync

class HostRecord(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    zone:str
    scope:str=Field(default='General',min_length=1,max_length=80)
    preferred_worker:str=''
    fallback_worker:str=''
    parameters:dict=Field(default_factory=dict)

FIELDS=('zone','scope','preferred_worker','fallback_worker','parameters')

def validate_routing(session,record):
    for field in ('preferred_worker','fallback_worker'):
        if not record.get(field):continue
        worker=session.get(Worker,record[field])
        if not worker or worker.zones!=[record['zone']] or record['scope'] not in worker.scopes:
            raise ValueError('Satellite '+record[field]+' must belong to host '+record['id']+' zone and support its scope')

def fingerprint(session):
    hosts=[svc.dump(h) for h in session.scalars(select(Host).order_by(Host.id))]
    zones=[svc.dump(z) for z in session.scalars(select(Zone).order_by(Zone.id))]
    return hashlib.sha256(json.dumps([hosts,zones,[(w.id,w.zones,w.scopes) for w in session.scalars(select(Worker).order_by(Worker.id))]],sort_keys=True).encode()).hexdigest()

def inventory_path(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('Inventory file path is required')
    path=Path(value).expanduser()
    if not path.is_absolute():path=ROOT/path
    if path.suffix.lower() not in ('.json','.yaml','.yml'):raise ValueError('Use a JSON or YAML inventory file')
    return path

def read_source(source):
    if not source['enabled']:raise ValueError('Inventory source is paused')
    if source['kind']!='file':raise ValueError('Use a file inventory source')
    path=inventory_path(source['config'].get('path'))
    with path.open('rb') as stream:raw=stream.read(1048577)
    if len(raw)>1048576:raise ValueError('Inventory payload exceeds 1 MB')
    if path.suffix.lower()=='.json':return json.loads(raw)
    import yaml
    return yaml.safe_load(raw)


def preview(source_id,payload):
    if not isinstance(payload,dict) or payload.get('version')!=1 or not isinstance(payload.get('hosts'),list):raise ValueError('Expected {"version":1,"hosts":[...]}')
    if len(payload['hosts'])>10000:raise ValueError('Maximum 10000 hosts per sync')
    records=[HostRecord.model_validate(record).model_dump() for record in payload['hosts']]
    active=current_zone.get()
    record_zones={r['zone'] for r in records}
    if len(record_zones)>1:raise ValueError('Use one inventory source per zone workspace')
    if active and any(r['zone']!=active for r in records):raise ValueError('Inventory hosts must belong to the current zone workspace')
    if len({r['id'] for r in records})!=len(records):raise ValueError('Duplicate host IDs in inventory payload')
    if len(json.dumps(records).encode())>1048576:raise ValueError('Inventory payload exceeds 1 MB')
    with db.transaction() as session:
        zones={z.id for z in session.scalars(select(Zone))};workers={w.id:w for w in session.scalars(select(Worker))}
        changes=[]
        for record in records:
            if record['zone'] not in zones:raise ValueError('Unknown zone: '+record['zone'])
            if any(record[k] and record[k] not in workers for k in ('preferred_worker','fallback_worker')):raise ValueError('Unknown satellite assignment for '+record['id'])
            validate_routing(session,record)
            host=session.get(Host,record['id'])
            if host and host.zone!=record['zone']:raise ValueError('Moving a host between workspaces is not allowed')
            if host and host.source_id not in ('',source_id):raise ValueError('Host owned by another source: '+host.id)
            diff={key:{'before':getattr(host,key) if host else None,'after':record[key]} for key in FIELDS if not host or getattr(host,key)!=record[key]}
            if host and not host.deleted and host.source_id!=source_id:diff['source_id']={'before':host.source_id,'after':source_id}
            changes.append({'id':record['id'],'state':'archived' if host and host.deleted else 'new' if not host else 'changed' if diff else 'unchanged','fields':diff})
        seen={r['id'] for r in records}
        for host in session.scalars(select(Host).where(Host.source_id==source_id,Host.deleted==False)):
            if host.id not in seen:changes.append({'id':host.id,'state':'missing','fields':{}})
        sync=InventorySync(zone=active or next(iter(record_zones),''),id=svc.uid(),source_id=source_id,created_at=time.time(),status='PREVIEW',payload={'records':records,'changes':changes,'baseline':fingerprint(session)})
        session.add(sync);session.flush();return sync_dump(sync)

def sync_dump(sync):
    return {'id':sync.id,'source_id':sync.source_id,'created_at':sync.created_at,'status':sync.status,'changes':sync.payload['changes']}

def apply(sync_id):
    with db.transaction() as session:
        sync=session.get(InventorySync,sync_id)
        if not sync:raise ValueError('Unknown inventory preview')
        if sync.status=='EXPIRED':raise ValueError('Preview expired during workspace migration; discover again')
        if sync.status=='APPLIED':return sync_dump(sync)
        if sync.payload['baseline']!=fingerprint(session):raise ValueError('Inventory changed since preview; discover again before applying')
        for record in sync.payload['records']:
            validate_routing(session,record)
            host=session.get(Host,record['id'])
            if host and host.deleted:continue
            if not host:host=Host(id=record['id'],enabled=False,discovered=True,deleted=False,source_id=sync.source_id);session.add(host)
            for key in FIELDS:setattr(host,key,record[key])
            host.source_id=sync.source_id
        session.flush();svc.validate_groups_and_schedules(session,require_targets=False)
        sync.status='APPLIED';return sync_dump(sync)
