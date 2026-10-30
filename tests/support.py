"""Populate isolated test databases. Not imported by the application or installers."""
from pathlib import Path
import yaml
from orchestrator import db,service
from orchestrator.models import Zone,Scope,Worker,Host,HostGroup,Collector,InventorySource,Schedule


def populate(include_schedules=False):
    directory=Path(__file__).parent/'fixtures/config'
    with db.transaction() as session:
        for filename,model in [('zones',Zone),('scopes',Scope),('satellites',Worker),('hosts',Host),('collectors',Collector),('host_groups',HostGroup),('inventory_sources',InventorySource)]:
            for record in yaml.safe_load((directory/(filename+'.yaml')).read_text()) or []:
                record=dict(record)
                if model is Worker:record['zones']=[record.pop('zone')]
                if not session.get(model,record['id']):session.add(model(**record))
            session.flush()
        if include_schedules:
            for record in yaml.safe_load((directory/'schedules.yaml').read_text()) or []:
                record=dict(record)
                groups=record.get('host_group_ids') or [record['host_group_id']]
                record.update(host_group_id=groups[0],host_group_ids=groups,created_at=service.now().timestamp())
                session.add(Schedule(**record))
                session.get(Collector,record['collector_id']).legacy_schedule=False
