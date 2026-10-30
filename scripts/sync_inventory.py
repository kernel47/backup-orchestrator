"""Refresh an optional API export, then preview/apply inventory changes on the master."""
import argparse,json,tempfile
from pathlib import Path
import httpx
from orchestrator.config import settings
from orchestrator.inventory_service import HostRecord,inventory_path,read_source


def collect_hosts():
    """Implement your API call and return [{id, zone, scope, parameters}, ...].

    Static inventory needs no implementation: omit --refresh.
    Keep API credentials in .env, never in the exported host file.
    """
    raise ValueError('Implement collect_hosts() before using --refresh; static files work without it.')


def refresh(path):
    records=[HostRecord.model_validate(row).model_dump() for row in collect_hosts()]
    if len(records)>10000 or len({r['id'] for r in records})!=len(records):raise ValueError('Maximum 10000 hosts with unique IDs')
    payload={'version':1,'hosts':records}
    if path.suffix.lower()=='.json':content=json.dumps(payload,indent=2)+'\n'
    else:
        import yaml
        content=yaml.safe_dump(payload,sort_keys=False)
    if len(content.encode())>1048576:raise ValueError('Inventory payload exceeds 1 MB')
    path.parent.mkdir(parents=True,exist_ok=True)
    # Replace only after a successful API call and validation; preserve the previous file on failure.
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name);stream.write(content)
    try:temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    print(f'Exported {len(records)} hosts to {path}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',default='inventory/hosts.json')
    parser.add_argument('--zone',help='Zone workspace (inferred for single-zone inventory files)')
    parser.add_argument('--refresh',action='store_true',help='Regenerate the file using collect_hosts() before discovery')
    parser.add_argument('--apply',action='store_true',help='Apply the preview; new hosts remain disabled')
    args=parser.parse_args();path=inventory_path(args.file)
    if args.refresh:refresh(path)
    # Validate the file before creating a source. No provider code runs in the dashboard.
    payload=read_source({'enabled':True,'kind':'file','config':{'path':str(path)}})
    if not isinstance(payload,dict) or payload.get('version')!=1 or not isinstance(payload.get('hosts'),list):raise ValueError('Expected {"version":1,"hosts":[...]}')
    zone=args.zone or next(iter({h['zone'] for h in payload['hosts']}),None) if len({h['zone'] for h in payload['hosts']})<=1 else args.zone
    if not zone:raise ValueError('Use one inventory file per zone and pass --zone for empty files')
    if any(h['zone']!=zone for h in payload['hosts']):raise ValueError('All inventory hosts must belong to --zone')
    with httpx.Client(base_url=settings.master_url,headers={'Authorization':'Bearer '+settings.admin_token,'X-Tasker-Zone':zone},timeout=30) as client:
        def request(method,url,**kwargs):
            response=client.request(method,url,**kwargs);response.raise_for_status();return response.json()
        sources=request('GET','/api/inventory/sources')
        source=next((s for s in sources if s['kind']=='file' and inventory_path(s['config'].get('path')).resolve()==path.resolve()),None)
        if source is None:source=request('POST','/api/inventory/sources',json={'name':path.stem+' inventory','kind':'file','config':{'path':str(path)}})
        preview=request('POST',f"/api/inventory/sources/{source['id']}/discover")
        from collections import Counter
        print('Changes:',dict(Counter(row['state'] for row in preview['changes'])))
        print('Preview:',preview['id'])
        if args.apply:
            request('POST',f"/api/inventory/syncs/{preview['id']}/apply")
            print('Applied. Activate new hosts in Inventory → Hosts.')
        else:print('Review in Inventory → Sources & discovery. Use --apply to apply changes.')

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,httpx.HTTPError) as error:
        raise SystemExit('Inventory sync failed: '+str(error))
