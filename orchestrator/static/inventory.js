/* Inventory membership and explicit collector/group targeting. */
const jsonParameters = form => {
  const parameters=JSON.parse(new FormData(form).get('parameters')||'{}');
  if(!parameters||Array.isArray(parameters)||typeof parameters!=='object')throw Error('Parameters must be a JSON object.');
  return parameters;
};
document.querySelectorAll('.host-group-form').forEach(form=>{
  const boxes=[...form.querySelectorAll('[name="host_ids"]')];
  const count=()=>form.querySelector('.member-count').textContent=`${boxes.filter(b=>b.checked).length} selected`;
  boxes.forEach(box=>box.addEventListener('change',count));
  form.querySelector('.member-search').addEventListener('input',event=>form.querySelectorAll('.host-member').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(event.target.value.toLowerCase())));
  form.addEventListener('submit',event=>{
    event.preventDefault();const f=new FormData(form),host_ids=boxes.filter(b=>b.checked).map(b=>b.value);
    if(!host_ids.length){toast('Select at least one host.',true);return;}
    action(form.querySelector('button'),'/api/host-groups'+(form.dataset.group?'/'+form.dataset.group:''),form.dataset.group?'PUT':'POST',{name:f.get('name'),description:f.get('description'),host_ids,enabled:f.get('enabled')!=='false'},()=> 'Host group saved.');
  });
});
const hostTable=document.querySelector('#host-table');
if(hostTable){
  const boxes=[...hostTable.querySelectorAll('[name="selected-host"]')],all=document.querySelector('#host-select-all');
  const activate=document.querySelector('#hosts-activate-selected'),disable=document.querySelector('#hosts-disable-selected');
  let busy=false;
  const visible=()=>boxes.filter(box=>!box.closest('tr').hidden&&!box.disabled);
  const updateSelection=()=>{
    const eligible=visible(),selected=eligible.filter(box=>box.checked);
    document.querySelector('#host-selected-count').textContent=`${selected.length} selected`;
    all.checked=eligible.length>0&&selected.length===eligible.length;
    all.indeterminate=selected.length>0&&selected.length<eligible.length;
    all.disabled=busy||eligible.length===0;
    activate.disabled=disable.disabled=busy||selected.length===0;
  };
  all.addEventListener('change',()=>{visible().forEach(box=>box.checked=all.checked);updateSelection();});
  boxes.forEach(box=>box.addEventListener('change',updateSelection));
  const changeSelected=async enabled=>{
    if(busy)return;
    const ids=visible().filter(box=>box.checked).map(box=>box.value);
    if(!ids.length)return;
    busy=true;updateSelection();
    try{
      const out=await api('/api/hosts/state','PUT',{ids,enabled});
      if(out){toast(`${out.changed} of ${out.total} host(s) ${enabled?'activated':'disabled'}.`);location.reload();}
    }catch(error){toast(error.message,true);}
    finally{busy=false;updateSelection();}
  };
  activate.addEventListener('click',()=>changeSelected(true));
  disable.addEventListener('click',()=>changeSelected(false));
  const filterHosts=()=>{
    const search=document.querySelector('#host-search').value.toLowerCase(),scope=document.querySelector('#host-scope').value,zone=document.querySelector('meta[name="workspace-zone"]').content,group=document.querySelector('#host-group').value,state=document.querySelector('#host-state').value;
    let count=0;
    hostTable.querySelectorAll('[data-host-row]').forEach(row=>{row.hidden=!(row.textContent.toLowerCase().includes(search)&&(!state||row.dataset.state===state)&&(!scope||row.dataset.scope===scope)&&(!zone||row.dataset.zone===zone)&&(!group||JSON.parse(row.dataset.groups).includes(group)));if(!row.hidden)count++;else row.querySelector('[name="selected-host"]').checked=false;});
    document.querySelector('#host-count').textContent=`${count} hosts`;document.querySelector('#host-empty').hidden=count>0;updateSelection();
  };
  document.querySelectorAll('.inventory-filter input,.inventory-filter select').forEach(input=>input.addEventListener(input.tagName==='SELECT'?'change':'input',filterHosts));filterHosts();
}
const selectedTargets=form=>({host_group_ids:[...form.querySelectorAll('[name="target_groups"]:checked')].map(e=>e.value),zones:[...form.querySelectorAll('[name="target_zones"]:checked')].map(e=>e.value)});
const launch=document.querySelector('#group-launch');
if(launch){
  const collector=launch.elements.collector_id,group={get value(){return selectedTargets(launch).host_group_ids.join(",");}},preview=document.querySelector('#target-preview'),run=document.querySelector('#group-run'),schedule=document.querySelector('#group-schedule');
  let version=0,readyKey='';
  const key=()=>collector.value+'|'+group.value+'|'+selectedTargets(launch).zones.join(',');
  const updatePreview=async()=>{
    const serial=++version;readyKey='';run.disabled=true;schedule.disabled=true;preview.replaceChildren();
    if(!collector.value||!group.value){preview.textContent='Choose a collector and group to preview targets.';return;}
    preview.textContent='Loading target preview…';
    try{
      const response=await fetch('/api/targets?'+new URLSearchParams({collector_id:collector.value,host_group_ids:group.value,zones:selectedTargets(launch).zones.join(",")}));const data=await response.json();if(serial!==version)return;
      if(!response.ok)throw Error(data.detail||'Unable to preview targets');
      preview.replaceChildren();const title=document.createElement('strong');title.textContent=`${data.hosts.length} compatible hosts · ${data.skipped.length} skipped · ${data.scope}`;preview.append(title);
      const interval=document.createElement('p');interval.className='hint';interval.textContent=`Run now uses the latest closed ${data.run_interval_seconds/60}-minute window. The interval selector is for the new schedule.`;preview.append(interval);
      const list=document.createElement('div');list.className='target-list';
      data.hosts.forEach(host=>{const entry=document.createElement('span');entry.className='quiet-chip';entry.textContent=host.id+' · '+host.zone;list.append(entry);});preview.append(list);
      data.skipped.forEach(item=>{const entry=document.createElement('p');entry.className='hint';entry.textContent=item.host+': '+item.reason;preview.append(entry);});
      if(data.hosts.length){readyKey=key();const role=document.body.dataset.userRole;run.disabled=role==='viewer';schedule.disabled=role!=='admin';}
    }catch(error){if(serial===version)preview.textContent=error.message;}
  };
  collector.addEventListener('change',updatePreview);launch.querySelectorAll('[name="target_groups"],[name="target_zones"]').forEach(input=>input.addEventListener('change',updatePreview));updatePreview();
  run.addEventListener('click',()=>{
    if(readyKey!==key())return;
    try{action(run,'/api/group-runs','POST',{collector_id:collector.value,...selectedTargets(launch),parameters:jsonParameters(launch)},out=>`${out.ids.length} collection windows submitted or reused; ${out.skipped.length} hosts skipped. Inspect Runs & history.`);}catch(error){toast(error.message,true);}
  });
  launch.addEventListener('submit',event=>{
    event.preventDefault();if(readyKey!==key())return;
    const f=new FormData(launch),name=String(f.get('name')||'').trim();if(!name){toast('Enter a schedule name.',true);launch.elements.name.focus();return;}
    try{action(schedule,'/api/schedules','POST',{name,collector_id:collector.value,...selectedTargets(launch),kind:f.get('kind'),local_time:f.get('local_time')||'07:00',interval_seconds:Number(f.get('interval_seconds')),parameters:jsonParameters(launch),enabled:true},()=> 'Schedule created.',out=>'/?view=schedules&schedule_id='+encodeURIComponent(out.id)+'&workspace='+encodeURIComponent(document.querySelector('meta[name="workspace-zone"]').content));}catch(error){toast(error.message,true);}
  });
}
document.querySelectorAll('.schedule-edit').forEach(form=>form.addEventListener('submit',event=>{
  event.preventDefault();const f=new FormData(form);
  try{action(form.querySelector('button'),'/api/schedules/'+form.dataset.schedule,'PUT',{name:f.get('name'),collector_id:f.get('collector_id'),...selectedTargets(form),kind:f.get('kind'),local_time:f.get('local_time')||'07:00',interval_seconds:Number(f.get('interval_seconds')),enabled:f.get('enabled')==='true',parameters:jsonParameters(form)},()=> 'Schedule saved.');}catch(error){toast(error.message,true);}
}));

document.querySelectorAll('#group-launch,.schedule-edit').forEach(form=>{const kind=form.querySelector('[name="kind"]');if(!kind)return;const update=()=>{const daily=kind.value==='daily_local';form.querySelector('[data-local-time-field]').hidden=!daily;form.querySelector('[data-interval-field]').hidden=daily;form.querySelector('[name="local_time"]').required=daily;};kind.addEventListener('change',update);update();});
