/* Progressive HTML navigation: the server remains the source of truth. */
(()=>{
  const root=()=>document.querySelector('#view-content');
  const enabled=()=>root()?.dataset.liveView==='true';
  const toggle=document.querySelector('#live-toggle'),refreshButton=document.querySelector('#live-refresh'),label=document.querySelector('#live-status');
  if(!enabled()||!toggle)return;
  let interval=12000,live=true,timer,controller,serial=0,busy=false,lastInteraction=0,failures=0;
  try{const value=localStorage.getItem('tasker.refresh');if(['0','12000','30000','60000'].includes(value))interval=Number(value);live=interval>0&&localStorage.getItem('tasker.live')!=='false';}catch(e){}
  const status=text=>label.textContent=text;
  const isLiveURL=url=>url.origin===location.origin&&(url.pathname==='/'&&['overview','collectors','runs'].includes(url.searchParams.get('view')||'collectors')||/^\/collections\/[^/]+$/.test(url.pathname));
  function protectedInteraction(){return Boolean(window.getSelection?.()?.toString())||document.hidden||window.taskerMutationPending>0||Date.now()-lastInteraction<8000||document.querySelector('dialog[open],.select-trigger[aria-expanded="true"],.tasker-calendar:not([hidden]),[name="collection"]:checked,#select-all:checked,details[open]:not(.advanced-filters)')||document.activeElement?.matches('input,select,textarea,.select-trigger,.date-trigger');}
  function schedule(){clearTimeout(timer);if(live)timer=setTimeout(tick,Math.min(60000,(interval||12000)*(failures+1)));}
  function controls(){toggle.textContent=`Live: ${live?'On':'Off'}`;toggle.setAttribute('aria-pressed',String(live));refreshButton.disabled=busy;}
  function render(next,doc,{poll=false,focusKind=''}={}){
    const previous=root(),scroll={x:scrollX,y:scrollY};
    // Keep filter inputs and their enhancement listeners intact during background updates.
    if(poll){const form=previous.querySelector('form.filters'),replacement=next.querySelector('form.filters');if(form&&replacement){
      const date=form.querySelector('input[type=date]'),freshDate=replacement.querySelector('input[type=date]');
      if(date&&freshDate&&date.value===date.defaultValue&&date.defaultValue!==freshDate.defaultValue){date.value=date.defaultValue=freshDate.defaultValue;date.dispatchEvent(new Event('change',{bubbles:true}));}
      replacement.replaceWith(form);
    }}
    const opened=[...previous.querySelectorAll('details')].map(d=>d.open);
    const tablePositions=[...previous.querySelectorAll('.table-scroll')].map(el=>el.scrollLeft);
    window.taskerResetSelects?.(previous);window.taskerResetCalendars?.(previous);
    previous.replaceWith(next);
    if(poll)next.querySelectorAll('details').forEach((d,i)=>{if(i<opened.length)d.open=opened[i];});
    window.taskerEnhanceSelects?.(next);window.taskerEnhanceCalendars?.(next);window.taskerRenderClocks?.();
    next.querySelectorAll('.table-scroll').forEach((el,i)=>el.scrollLeft=tablePositions[i]||0);
    document.title=doc.title;
    // Workspace parameters are added to client URLs; use stable page keys instead.
    const activeKeys=new Set([...doc.querySelectorAll('.sidebar nav a[aria-current="page"]')].map(a=>a.dataset.navKey));
    document.querySelectorAll('.sidebar nav a[data-nav-key]').forEach(a=>{
      const active=activeKeys.has(a.dataset.navKey);
      a.classList.toggle('active',active);
      if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    });
    if(focusKind)next.querySelector(focusKind)?.focus({preventScroll:true});
    scrollTo(scroll.x,scroll.y);
    document.body.classList.remove('sidebar-expanded');
    document.dispatchEvent(new CustomEvent('tasker:render'));
  }
  async function load(url,{poll=false,push=false,force=false,focusKind=''}={}){
    if(poll&&(busy||!force&&protectedInteraction()))return false;
    controller?.abort();const mine=++serial;controller=new AbortController();const requestController=controller;busy=true;controls();
    const timeout=setTimeout(()=>requestController.abort(),15000);
    if(!poll)root().setAttribute('aria-busy','true');
    try{
      const response=await fetch(url,{signal:controller.signal,cache:'no-store',headers:{'X-Tasker-View':'1'}});
      if(mine!==serial)return false;
      const responseURL=new URL(response.url);
      if(responseURL.pathname==='/login'){location.assign('/login?next='+encodeURIComponent(url.pathname+url.search));return false;}
      if(!response.ok)throw Error(`Unable to update (${response.status}).`);
      const doc=new DOMParser().parseFromString(await response.text(),'text/html'),fragment=doc.querySelector('#view-content');
      if(!fragment||fragment.dataset.liveView!=='true')throw Error('This view cannot be updated in place.');
      if(mine!==serial||poll&&!force&&protectedInteraction())return false;
      render(document.importNode(fragment,true),doc,{poll,focusKind});
      if(push&&url.href!==location.href)history.pushState(null,'',responseURL.pathname+responseURL.search+responseURL.hash);
      failures=0;status(`Updated ${new Date().toLocaleTimeString()}${live?' · Live':' · Paused'}`);
      return true;
    }catch(error){
      if(mine!==serial)return false;
      if(error.name==='AbortError'){if(!poll)status('Update cancelled or timed out. Try Refresh now.');}
      else{failures=Math.min(failures+1,4);status('Connection unavailable · showing last results');if(!poll)toast(error.message,true);}
      return false;
    }finally{
      clearTimeout(timeout);
      if(mine===serial){busy=false;root()?.removeAttribute('aria-busy');controls();schedule();}
    }
  }
  async function tick(){
    if(!live)return;
    if(busy||protectedInteraction()){schedule();return;}
    await load(new URL(location.href),{poll:true});
  }
  window.TaskerLive={enabled,refresh:options=>load(new URL(location.href),{poll:!options?.force}),navigate:url=>load(new URL(url,location.href),{push:true})};
  toggle.addEventListener('click',()=>{live=!live;try{if(live&&!interval){interval=12000;localStorage.setItem('tasker.refresh','12000');}localStorage.setItem('tasker.live',String(live));}catch(e){}controls();status(live?'Live updates enabled':'Live updates paused');schedule();});
  refreshButton.addEventListener('click',()=>load(new URL(location.href),{poll:true,force:true}));
  document.addEventListener('click',event=>{
    if(event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const anchor=event.target.closest('a[href]');if(!anchor||anchor.target||anchor.hasAttribute('download'))return;
    const url=new URL(anchor.href);if(!isLiveURL(url)||url.hash)return;
    event.preventDefault();lastInteraction=Date.now();
    const focusKind=anchor.closest('.status-tabs')?'.status-tabs [aria-current]':'';
    load(url,{push:true,focusKind});
  });
  document.addEventListener('submit',event=>{
    const form=event.target;if(event.defaultPrevented||!form.matches('#view-content form.filters,#view-content form.pagination-size')||(form.method||'get').toLowerCase()!=='get')return;
    const url=new URL(form.action||location.href);url.search=new URLSearchParams(new FormData(form)).toString();
    if(!isLiveURL(url))return;event.preventDefault();lastInteraction=Date.now();load(url,{push:true});
  });
  window.addEventListener('popstate',()=>{const url=new URL(location.href);if(isLiveURL(url))load(url);else location.reload();});
  ['input','change','pointerdown','keydown','wheel','touchstart','scroll'].forEach(name=>document.addEventListener(name,()=>{lastInteraction=Date.now();},{passive:true}));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)schedule();});
  controls();if(!live)status('Live updates paused');schedule();
})();
