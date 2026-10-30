import asyncio,time,secrets
from collections import Counter,defaultdict
from contextlib import asynccontextmanager,suppress
from datetime import timedelta
import calendar
from fastapi import Query
from fastapi import FastAPI,Request,Depends,Header,HTTPException,Form
from fastapi.responses import RedirectResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .config import ROOT,settings,load
from .cadences import CADENCES,SCHEDULE_CADENCES,cadence_label
from .scheduling import local_label
from .views import latest_collector_health,overview_activity
from .workspaces import current_zone,allowed_zones,require_zone
from . import db,service as svc
from .models import *
from .schemas import *
from .security import admin,worker_auth,issue_session,web_token
from . import security
from .inventory_api import router as inventory_router
from .inventory_api import sources as inventory_sources,syncs as inventory_syncs,sync_detail as inventory_sync_detail

last_scheduler_tick=time.monotonic()
async def scheduler():
    global last_scheduler_tick
    while True:
        try:
            await asyncio.to_thread(svc.tick)
            last_scheduler_tick=time.monotonic()
        except Exception:
            import logging
            logging.getLogger("scheduler").exception("Scheduler tick failed; retrying in 2 seconds")
        await asyncio.sleep(2)
@asynccontextmanager
async def lifespan(app):
    db.init();svc.bootstrap()
    task=asyncio.create_task(scheduler()) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):await task
app=FastAPI(title="Tasker · Collector Orchestrator",version="0.1.0",lifespan=lifespan)
@app.middleware("http")
async def require_login(request:Request,call_next):
    path=request.url.path
    parts=path.split('/')
    worker_route=path in ('/api/workers/heartbeat','/api/workers/claim') or (len(parts)==5 and parts[1:3]==['api','tasks'] and parts[4] in ('heartbeat','result'))
    public=path in ('/login','/health') or path.startswith('/static/') or worker_route
    if not public and not (web_token(request) or security.api_admin(request.headers.get('authorization',''))):
        if path.startswith('/api/') or path=='/openapi.json':return JSONResponse({'detail':'Sign in required'},401,headers={'Cache-Control':'no-store'})
        from urllib.parse import urlencode
        destination=path+('?' + request.url.query if request.url.query else '')
        return RedirectResponse('/login?'+urlencode({'next':destination}),303,headers={'Cache-Control':'no-store'})
    token=None;allowed_token=None
    request.state.user=security.principal(request) if not worker_route and path!='/health' and not path.startswith('/static/') else None
    request.state.workspace_zones=[];request.state.zone=''
    if not public:
        with db.Session() as session:
            available=[svc.dump(z) for z in session.scalars(select(Zone))]
        user=request.state.user
        if user and user['zones'] is not None:available=[z for z in available if z['id'] in user['zones']]
        request.state.workspace_zones=available
        if user and user['zones'] is not None and not available:return JSONResponse({'detail':'No authorized workspace. Contact the global administrator.'},403)
        machine=security.api_admin(request.headers.get('authorization',''))
        selected=request.headers.get('x-tasker-zone') or request.query_params.get('workspace') or (request.query_params.get('zone') if path in ('/','/api/collections','/api/collections/summary') else '') or ('' if machine else request.cookies.get('workspace',''))
        if not selected and not machine and available:selected=available[0]['id']
        if selected and selected not in {z['id'] for z in available}:return JSONResponse({'detail':'Unknown or unauthorized zone workspace'},404)
        if not selected and not machine and path not in ('/','/api/zones','/logout','/workspace','/docs','/openapi.json'):
            return JSONResponse({'detail':'Create your first zone workspace'},409)
        request.state.zone=selected
        token=current_zone.set(selected or None)
        if user and user['zones'] is not None:allowed_token=allowed_zones.set(user['zones'])
    try:response=await call_next(request)
    finally:
        if token is not None:current_zone.reset(token)
        if allowed_token is not None:allowed_zones.reset(allowed_token)
    if getattr(request.state,'zone','') and path=='/':response.set_cookie('workspace',request.state.zone,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=31536000)
    if not path.startswith('/static/'):response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(IntegrityError)
async def conflict(request,error):return JSONResponse({'detail':'ID already exists or an object is still referenced. Choose another ID.'},409)

app.include_router(inventory_router)
app.mount("/static",StaticFiles(directory=ROOT/"orchestrator/static"),name="static")
templates=Jinja2Templates(directory=ROOT/"orchestrator/templates")
templates.context_processors.append(lambda request:{'active_zone':getattr(request.state,'zone',''),'workspace_zones':getattr(request.state,'workspace_zones',[]),'auth_username':request.state.user['username'] if getattr(request.state,'user',None) else settings.auth_username,'user_role':request.state.user['role'] if getattr(request.state,'user',None) else 'admin'})
templates.env.globals.update(cadence_label=cadence_label,workspace_name=settings.workspace_name,local_label=local_label,auth_username=settings.auth_username)
def filters(request:Request,date:str="",zone:str="",scope:str="",collector_id:str="",host:str="",status:str="",worker:str="",host_group_id:str="",schedule_id:str="",start:str="",end:str="",hour:str=""):
    zone=current_zone.get() or zone
    legacy=request.query_params.get("technology","")
    if legacy:
        if scope and scope!=legacy:raise HTTPException(422,"Conflicting scope and legacy technology filters")
        scope=legacy
    if "region" in request.query_params:raise HTTPException(422,"Use the zone filter instead of region")
    if date:
        try:svc.bounds(date)
        except ValueError:raise HTTPException(422,"Expected date: YYYY-MM-DD")
    if hour:
        if not hour.isdigit() or not 0<=int(hour)<24:raise HTTPException(422,"Hour must be between 0 and 23")
        anchor=svc.dt((date or svc.now().strftime('%Y-%m-%d'))+'T00:00:00Z')
        if request.query_params.get('period','day')=='day':start=svc.iso(anchor+timedelta(hours=int(hour)));end=svc.iso(anchor+timedelta(hours=int(hour)+1))
    return {k:v for k,v in {'date':date,'zone':zone,'scope':scope,'collector_id':collector_id,'host':host,'status':status,'worker':worker,'host_group_id':host_group_id,'schedule_id':schedule_id,'start':start,'end':end}.items() if v}
@app.get("/health")
def health():
    try:
        with db.Session() as session:session.execute(select(Zone.id).limit(1)).first()
    except Exception:return JSONResponse({'status':'error','database':'unavailable'},503)
    scheduler_ok=not settings.scheduler_enabled or time.monotonic()-last_scheduler_tick<30
    data={"status":"ok" if scheduler_ok else "error","product":"Tasker","version":"0.1.0","database":"ok","scheduler":settings.scheduler_enabled,"scheduler_healthy":scheduler_ok}
    return data if scheduler_ok else JSONResponse(data,503)
@app.get("/api/catalog")
def catalog():
    with db.transaction() as s:
        scopes=list(dict.fromkeys([c.scope for c in s.scalars(select(Collector))]+[h.scope for h in s.scalars(select(Host).where(Host.deleted==False))]+[scope for w in s.scalars(select(Worker)) for scope in w.scopes]+list(s.scalars(select(Scope.id)))))
        return {"scopes":scopes,"zones":[svc.dump(z) for z in s.scalars(select(Zone))],"cadences":[{"seconds":seconds,"label":label} for seconds,label in CADENCES],"scripts":list(load("worker_scripts.yaml"))}

@app.get("/api/hosts")
def hosts(include_archived:bool=False):
    with db.transaction() as s:return [svc.dump(h) for h in s.scalars(select(Host)) if include_archived or not h.deleted]
@app.get("/api/workers")
def workers():
    with db.transaction() as s:
        active=Counter(a.worker_id for a in s.scalars(select(Attempt).where(Attempt.status=="RUNNING")))
        next_runs={}
        for rule in s.scalars(select(Schedule).where(Schedule.enabled==True)):
            if not s.get(Collector,rule.collector_id).enabled:continue
            for timing in svc.schedule_timing(s,rule):
                wid=timing["worker_id"]
                if wid not in next_runs or timing["next_execution"]<next_runs[wid]["next_execution"]:next_runs[wid]=dict(timing,schedule=rule.name)
        return [dict(svc.dump(w),online=time.time()-w.heartbeat<=settings.offline_seconds,active=active[w.id],last_seen_seconds=max(0,int(time.time()-w.heartbeat)) if w.heartbeat else None,local_time=local_label(svc.now(),w.timezone),clock_epoch=svc.now().timestamp()*1000,next_run=next_runs.get(w.id)) for w in s.scalars(select(Worker))]
@app.get("/api/host-groups")
def host_groups():
    with db.transaction() as s:return [svc.dump(g) for g in s.scalars(select(HostGroup).where(HostGroup.deleted==False).order_by(HostGroup.name))]

@app.post("/api/host-groups",dependencies=[Depends(admin)])
def create_group(body:HostGroupInput):
    return save_group(None,body)

@app.put("/api/host-groups/{group_id}",dependencies=[Depends(admin)])
def update_group(group_id:str,body:HostGroupInput):
    return save_group(group_id,body)

def save_group(group_id,body):
    try:
        with db.transaction() as s:
            ids=list(dict.fromkeys(body.host_ids))
            if any(not s.get(Host,hid) or s.get(Host,hid).deleted for hid in ids):raise HTTPException(422,"Unknown host in group")
            group=s.get(HostGroup,group_id) if group_id else HostGroup(id=svc.uid())
            if not group:raise HTTPException(404,"Unknown host group")
            member_zones={s.get(Host,hid).zone for hid in ids}
            if len(member_zones)!=1:raise ValueError("A host group must belong to exactly one zone")
            group.zone=next(iter(member_zones));require_zone(group.zone)
            group.name=body.name;group.description=body.description;group.host_ids=ids;group.enabled=body.enabled;group.deleted=False;s.add(group);s.flush()
            svc.validate_groups_and_schedules(s,require_targets=False)
            return svc.dump(group)
    except ValueError as error:raise HTTPException(422,str(error))

@app.get("/api/schedules")
def schedules():
    with db.transaction() as s:return [dict(svc.dump(rule),timing=svc.schedule_timing(s,rule)) for rule in s.scalars(select(Schedule).order_by(Schedule.name))]

@app.post("/api/schedules",dependencies=[Depends(admin)])
def create_schedule(body:ScheduleInput):return save_schedule(None,body)

@app.put("/api/schedules/{schedule_id}",dependencies=[Depends(admin)])
def update_schedule(schedule_id:str,body:ScheduleInput):return save_schedule(schedule_id,body)

def save_schedule(schedule_id,body):
    for z in body.zones:require_zone(z)
    if current_zone.get():body.zones=[current_zone.get()]
    try:
        with db.transaction() as s:
            gids=list(dict.fromkeys(body.host_group_ids or ([body.host_group_id] if body.host_group_id else [])))
            c,targets,_=svc.resolve_targets(s,body.collector_id,gids,body.zones,allow_inactive=not body.enabled)
            if len({h.zone for h in targets})>1:raise ValueError("Schedules cannot span zone workspaces")
            if body.enabled and not targets:raise ValueError("No enabled hosts match this collector scope")
            rule=s.get(Schedule,schedule_id) if schedule_id else Schedule(id=svc.uid(),created_at=svc.now().timestamp())
            if not rule:raise HTTPException(404,"Unknown schedule")
            if schedule_id and rule.collector_id!=body.collector_id:raise ValueError("Create a new schedule to change its collector")
            changed=schedule_id and (rule.interval_seconds!=body.interval_seconds or rule.kind!=body.kind or rule.local_time!=body.local_time or set(svc.rule_group_ids(rule))!=set(gids) or set(rule.zones)!=set(body.zones))
            if schedule_id and (rule.kind!=body.kind or rule.local_time!=body.local_time):rule.created_at=svc.now().timestamp()
            for k,v in body.model_dump().items():setattr(rule,k,v)
            if body.enabled and body.kind=="daily_local" and any(not svc.clock_worker(s,h) for h in targets):
                raise ValueError("Every target needs an authorized satellite reporting its local timezone. Start the workers first.")
            rule.zone=targets[0].zone if targets else s.get(HostGroup,gids[0]).zone;rule.zones=[rule.zone];require_zone(rule.zone)
            rule.host_group_id=gids[0];rule.host_group_ids=gids
            s.add(rule);s.flush();svc.validate_groups_and_schedules(s,require_targets=False)
            if changed:
                for w in s.scalars(select(Window).where(Window.collector_id==c.id,Window.status.in_(["SCHEDULED","DISABLED"]))):
                    if w.snapshot.get("schedule_id")==rule.id and svc.dt(w.period_end)>svc.now():s.delete(w)
            if rule.enabled and c.legacy_schedule:
                c.legacy_schedule=False
                for w in s.scalars(select(Window).where(Window.collector_id==c.id,Window.status=="SCHEDULED")):
                    if not w.snapshot.get("schedule_id") and svc.dt(w.period_end)>svc.now():s.delete(w)
            return svc.dump(rule)
    except ValueError as error:raise HTTPException(422,str(error))

@app.get("/api/targets")
def targets(collector_id:str,host_group_id:str="",host_group_ids:str="",zones:str=""):
    try:
        with db.transaction() as s:
            gids=[g for g in host_group_ids.split(",") if g] or ([host_group_id] if host_group_id else [])
            selected_zones=[z for z in zones.split(",") if z]
            if current_zone.get():
                for z in selected_zones:require_zone(z)
                selected_zones=[current_zone.get()]
            c,hosts,skipped=svc.resolve_targets(s,collector_id,gids,selected_zones)
            rule=next((r for r in s.scalars(select(Schedule).where(Schedule.collector_id==c.id,Schedule.enabled==True)) if set(svc.rule_group_ids(r))==set(gids) and set(r.zones)==set(selected_zones)),None)
            return {"collector":c.name,"scope":c.scope,"groups":gids,"hosts":[svc.dump(h) for h in hosts],"skipped":skipped,"run_interval_seconds":rule.interval_seconds if rule else c.interval_seconds}
    except ValueError as error:raise HTTPException(422,str(error))

@app.post("/api/group-runs",dependencies=[Depends(admin)])
def group_run(body:GroupRunRequest):
    for z in body.zones:require_zone(z)
    if current_zone.get():body.zones=[current_zone.get()]
    try:return svc.run_group(body.collector_id,body.host_group_id,body.parameters,body.host_group_ids,body.zones)
    except ValueError as error:raise HTTPException(422,str(error))

@app.get("/api/collectors")
def collectors():
    with db.transaction() as s:return [svc.dump(c) for c in s.scalars(select(Collector))]
@app.post("/api/collectors",dependencies=[Depends(admin)])
def create_collector(body:CollectorCreate):
    cat=catalog()
    if body.scope not in cat["scopes"]:raise HTTPException(422,"Scope is not registered by an inventory host or satellite")
    if body.script not in cat["scripts"]:raise HTTPException(422,"Script is not in the allowed catalog")
    with db.transaction() as s:
        if s.get(Collector,body.id):raise HTTPException(409,"Collector ID already exists")
        require_zone(body.zone)
        if not (body.zone or current_zone.get()):body.zone=cat['zones'][0]['id'] if cat['zones'] else ''
        values=body.model_dump();values['zone']=body.zone or current_zone.get()
        if not s.get(Zone,values['zone']):raise HTTPException(422,'Unknown zone workspace')
        # Names may repeat across workspaces; internal identifiers stay unambiguous.
        existing=s.scalar(select(Collector).where(Collector.id==body.id).execution_options(workspace_global=True))
        if existing and existing.zone!=values['zone']:values['id']=values['zone'].lower()+'__'+body.id
        c=Collector(**values,revision=1,legacy_schedule=False);s.add(c);s.flush();return svc.dump(c)
@app.put("/api/collectors/{collector_id}",dependencies=[Depends(admin)])
def update_collector(collector_id:str,body:CollectorUpdate):
    with db.transaction() as s:
        c=s.get(Collector,collector_id)
        if not c:raise HTTPException(404,"Unknown collector")
        for k,v in body.model_dump().items():setattr(c,k,v)
        c.revision+=1
        # Future windows can use the new cadence; historical/active windows remain immutable.
        for w in s.scalars(select(Window).where(Window.collector_id==c.id,Window.status.in_(["SCHEDULED","DISABLED"]))):
            if not w.snapshot.get("schedule_id") and svc.dt(w.period_end)>svc.now():s.delete(w)
        return svc.dump(c)
@app.post("/api/inventory/sync",dependencies=[Depends(admin)])
def sync():raise HTTPException(409,"Use Inventory sources: discover changes, then apply a preview. Startup never overwrites managed hosts.")
@app.get("/api/collections")
def collections(f:dict=Depends(filters)):
    with db.transaction() as s:
        svc.recover(s)
        if f.get("date"):svc.expected(s,f["date"],f.get("collector_id"),f.get("host",""),zone=f.get("zone",""))
        return svc.rows(s,f)
@app.get("/api/collections/summary")
def summary(f:dict=Depends(filters)):
    f.pop("status",None);rs=collections(f)
    return {"expected":sum(r["status"] not in ("SCHEDULED","DISABLED") for r in rs),"counts":dict(Counter(r["status"] for r in rs))}
@app.get("/api/collections/{collection_id}")
def collection(collection_id:str):
    with db.transaction() as s:
        w=s.get(Window,collection_id)
        if not w:raise HTTPException(404,"Unknown collection")
        return next(r for r in svc.rows(s,{"date":w.period_start[:10],"collector_id":w.collector_id}) if r["id"]==collection_id)
@app.post("/api/collections/plan",dependencies=[Depends(admin)])
def plan(body:PlanRequest):
    require_zone(body.zone)
    body.zone=current_zone.get() or body.zone
    try:
        with db.transaction() as s:
            if not s.get(Collector,body.collector_id):raise HTTPException(404,"Unknown collector")
            gids=[body.host_group_id] if body.host_group_id else []
            zones=[body.zone] if body.zone else []
            if body.schedule_id:
                rule=s.get(Schedule,body.schedule_id)
                if not rule or rule.collector_id!=body.collector_id:raise ValueError("Schedule does not match collector")
                if gids and gids[0] not in svc.rule_group_ids(rule):raise ValueError("Schedule does not match host group")
                gids=gids or svc.rule_group_ids(rule)
                if zones and rule.zones and zones[0] not in rule.zones:raise ValueError("Zone outside schedule targets")
                zones=zones or rule.zones
            if gids:
                c,targets,skipped=svc.resolve_targets(s,body.collector_id,gids,zones)
                ids=[]
                for h in targets:
                    if body.host and h.id!=body.host:continue
                    ids.extend(svc.expected(s,body.date,c.id,h.id,True,body.parameters,zone=body.zone))
                for cid in ids:
                    w=s.get(Window,cid)
                    if w.status=="PENDING" and not w.snapshot.get("host_group_id") and not s.scalar(select(Attempt).where(Attempt.window_id==cid)):
                        memberships=[gid for gid in gids if w.host_id in s.get(HostGroup,gid).host_ids]
                        w.snapshot=dict(w.snapshot,host_group_id=memberships[0],host_group_ids=memberships)
                return {"ids":list(dict.fromkeys(ids)),"skipped":skipped}
            return {"ids":svc.expected(s,body.date,body.collector_id,body.host,True,body.parameters,zone=body.zone)}
    except ValueError as error:raise HTTPException(422,str(error))
@app.post("/api/collections/retry",dependencies=[Depends(admin)])
def retry(body:RetryRequest):
    require_zone(body.filters.get('zone'))
    if current_zone.get():body.filters['zone']=current_zone.get()
    return {"ids":svc.retry(body.filters,body.ids,body.mode)}
@app.post("/api/workers/heartbeat")
def heartbeat(body:WorkerHeartbeat,authorization:str=Header(default="")):
    wid=worker_auth(body.worker_id,authorization)
    with db.transaction() as s:
        worker=s.get(Worker,wid);worker.heartbeat=time.time()
        if body.timezone is not None:
            if worker.timezone and worker.timezone!=body.timezone:
                for window in s.scalars(select(Window).where(Window.status.in_(["SCHEDULED","PENDING"]))):
                    if window.snapshot.get("schedule_kind")=="daily_local" and window.snapshot.get("clock_worker_id")==wid and svc.dt(window.period_end)>svc.now() and not s.scalar(select(Attempt.id).where(Attempt.window_id==window.id)):
                        s.delete(window)
            worker.timezone=body.timezone
    return {"ok":True}
@app.post("/api/workers/claim")
def claim(body:WorkerRequest,authorization:str=Header(default="")):
    return svc.claim(worker_auth(body.worker_id,authorization))
@app.post("/api/tasks/{task_id}/heartbeat")
def task_heartbeat(task_id:str,body:WorkerRequest,authorization:str=Header(default="")):
    wid=worker_auth(body.worker_id,authorization)
    with db.transaction() as s:
        a=s.get(Attempt,task_id)
        if not a or a.worker_id!=wid or a.status!="RUNNING" or time.time()>a.lease_until:raise HTTPException(409,"Expired lease")
        a.lease_until=time.time()+settings.lease_seconds;s.get(Worker,wid).heartbeat=time.time()
    return {"ok":True}
@app.post("/api/tasks/{task_id}/result")
def result(task_id:str,body:ResultRequest,authorization:str=Header(default="")):
    wid=worker_auth(body.worker_id,authorization)
    try:return svc.result(task_id,wid,body.result.model_dump(mode="json"),body.logs)
    except ValueError as error:raise HTTPException(409,str(error))
def login_page(request,error="",status_code=200,username="",next_url="/?view=overview"):
    challenge=issue_session('login')
    response=templates.TemplateResponse(request=request,name="login.html",context={"request":request,"error":error,"username":username,"next_url":security.safe_next(next_url),"challenge":challenge,"configured":bool(settings.auth_password)},status_code=status_code)
    response.set_cookie("login_csrf",challenge,httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=1800,path="/login")
    response.headers["Cache-Control"]="no-store"
    return response

@app.get("/login")
def login_form(request:Request,next:str="/?view=overview"):
    if web_token(request):return RedirectResponse(security.safe_next(next),303)
    return login_page(request,next_url=next)

@app.post("/login")
def login(request:Request,username:str=Form(default="",max_length=80),password:str=Form(default="",max_length=1024),csrf:str=Form(default="",max_length=256),next:str=Form(default="/?view=overview",max_length=4096)):
    challenge=request.cookies.get("login_csrf","")
    if not security.valid(challenge,'login',1800) or not secrets.compare_digest(csrf.encode(),challenge.encode()):
        return login_page(request,"This sign-in form expired. Please try again.",403,username,next)
    key=request.client.host if request.client else 'unknown'
    if security.login_limited(key):
        response=login_page(request,"Too many sign-in attempts. Please try again in a few minutes.",429,username,next)
        response.headers["Retry-After"]="300"
        return response
    if not security.credentials_match(username,password):
        security.login_failure(key)
        return login_page(request,"Invalid username or password.",401,username,next)
    security.login_success(key)
    response=RedirectResponse(security.safe_next(next),303)
    response.set_cookie("session",issue_session(username=username),httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=43200,path="/")
    response.delete_cookie("login_csrf",path="/login")
    response.headers["Cache-Control"]="no-store"
    return response

@app.post("/workspace")
def switch_workspace(request:Request,zone_id:str=Form(...),csrf:str=Form(default='')):
    token=web_token(request)
    if not token or not secrets.compare_digest(csrf.encode(),token.encode()):raise HTTPException(403,'Invalid workspace request')
    if zone_id not in {z['id'] for z in request.state.workspace_zones}:raise HTTPException(404,'Unknown workspace')
    response=RedirectResponse('/?view=overview&workspace='+zone_id,303)
    response.set_cookie('workspace',zone_id,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=31536000)
    return response

@app.post("/logout")
def logout(request:Request,csrf:str=Form(default="")):
    token=web_token(request)
    if not token or not secrets.compare_digest(csrf.encode(),token.encode()):raise HTTPException(403,"Invalid sign-out request")
    response=RedirectResponse("/login",303);response.delete_cookie("session",path="/")
    response.headers["Cache-Control"]="no-store"
    return response

@app.get("/")
def dashboard(request:Request,f:dict=Depends(filters),view:str="collectors",period:str|None=Query(None,pattern="^(day|week|month)$"),page:int=Query(1,ge=1),page_size:int=Query(25,ge=10,le=100),section:str=Query("collectors",pattern="^(collectors|inventory)$")):
    period=period or ('week' if view=='collectors' and not f.get('collector_id') else 'day')
    if not request.state.workspace_zones:
        return templates.TemplateResponse(request=request,name="administration.html",context={"request":request,"view":"zones","catalog":{"zones":[],"scopes":[]},"csrf":web_token(request)})
    if view=="overview":
        # A read-only summary: never plan windows or apply URL filters here.
        today=svc.now().strftime("%Y-%m-%d")
        with db.Session() as session:
            totals=Counter(session.scalars(select(Window.status).where(Window.period_start>=today+"T00:00:00Z",Window.period_start<svc.iso(svc.dt(today+"T00:00:00Z")+timedelta(days=1)))))
            health=latest_collector_health(session)
            activity=overview_activity(session)
        return templates.TemplateResponse(request=request,name="overview.html",context={"request":request,"view":"overview","overview":True,"collectors":collectors(),"hosts":hosts(),"groups":host_groups(),"catalog":catalog(),"today":today,"counts":totals,"health":health,**activity,"csrf":web_token(request)})
    if view=='settings' and section=='inventory':
        return RedirectResponse(str(request.url.include_query_params(view='hosts')),303)
    if view=='settings' and section=='collectors':
        cs=collectors();cat=catalog();selected_id=request.query_params.get('collector_id')
        selected=next((c for c in cs if c['id']==selected_id),None)
        if selected_id and not selected:raise HTTPException(404,'Collector not found in this workspace')
        creating=request.query_params.get('new')=='1' and not selected
        names=list(dict.fromkeys(cat['scopes']+[c['scope'] for c in cs]))
        if not selected and not creating and names:
            f.setdefault('scope',names[0])
            if f['scope'] not in names:raise HTTPException(422,'Unknown scope')
        return templates.TemplateResponse(request=request,name='collector_settings.html',context={
            'request':request,'view':'settings','filters':f,'collectors':cs,'catalog':cat,
            'scope_names':names,'selected':selected,'creating':creating,
            'editing':bool(selected and request.query_params.get('mode')=='edit'),
            'cadences':CADENCES,'csrf':web_token(request)})
    if view in ("workspace","scopes"):
        return templates.TemplateResponse(request=request,name="configuration.html",context={"request":request,"view":view,"catalog":catalog(),"csrf":web_token(request)})
    if view in ("zones","sources","workers"):
        history=inventory_syncs() if view=="sources" else []
        selected=request.query_params.get("sync_id") or (history[0]["id"] if history else "")
        sync=inventory_sync_detail(selected) if selected else None
        changes_total=len(sync["changes"]) if sync else 0
        pages=max(1,(changes_total+page_size-1)//page_size);page=min(page,pages)
        if sync:sync["changes"]=sync["changes"][(page-1)*page_size:page*page_size]
        return templates.TemplateResponse(request=request,name="administration.html",context={"request":request,"view":view,"catalog":catalog(),"workers":workers(),"sources":inventory_sources(),"syncs":[sync] if sync else [],"sync_history":history,"changes_total":changes_total,"page":page,"pages":pages,"page_size":page_size,"csrf":web_token(request)})
    if view in ("hosts","groups","schedules"):
        hs=hosts(include_archived=True);groups=host_groups();rules=schedules();cs=collectors()
        if view=='schedules' and request.query_params.get('schedule_id') and not any(r['id']==request.query_params['schedule_id'] for r in rules):raise HTTPException(404,'Schedule not found in this workspace')
        return templates.TemplateResponse(request=request,name="schedules.html" if view=='schedules' else "inventory.html",context={"request":request,"view":view,"hosts":hs,"groups":groups,"schedules":rules,"collectors":cs,"catalog":catalog(),"cadences":SCHEDULE_CADENCES,"csrf":web_token(request)})
    if view=='collectors' and f.get('collector_id'):
        return RedirectResponse(str(request.url.include_query_params(view='runs')),303)
    overview=view=="overview"
    if overview:view="collectors"
    cs=collectors();cat=catalog()
    scope_tabs=(view=="collectors" and not overview and not f.get("collector_id")) or (view=="settings" and section=="collectors")
    scope_names=list(dict.fromkeys(cat["scopes"]+[c["scope"] for c in cs]))
    if scope_tabs and scope_names:
        f.setdefault("scope",scope_names[0])
        if f["scope"] not in scope_names:raise HTTPException(422,"Unknown scope")
    f.setdefault("date",svc.now().strftime("%Y-%m-%d"))
    anchor=svc.dt(f["date"]+"T00:00:00Z")
    begin=anchor if period=="day" else anchor-timedelta(days=anchor.weekday()) if period=="week" else anchor.replace(day=1)
    length=1 if period=="day" else 7 if period=="week" else calendar.monthrange(anchor.year,anchor.month)[1]
    finish=begin+timedelta(days=length)
    if period!="day":
        f.pop("date",None);f["start"]=svc.iso(begin);f["end"]=svc.iso(finish)
    with db.transaction() as session:
        if view not in ('settings','workers'):
            svc.recover(session)
            if f.get('date'):svc.expected(session,f['date'],f.get('collector_id'),f.get('host',''),zone=f.get('zone',''))
            rs=svc.rows(session,f,include_attempts=False)
            all_rs=svc.rows(session,{k:v for k,v in f.items() if k!='status'},include_attempts=False) if f.get('status') else rs
        else:rs=[];all_rs=[]
    selected_hour=next((h for h in range(24) if period=='day' and f.get('start')==svc.iso(anchor+timedelta(hours=h)) and f.get('end')==svc.iso(anchor+timedelta(hours=h+1))),None)
    counts=Counter(r["status"] for r in all_rs)
    expected=sum(v for k,v in counts.items() if k not in ("SCHEDULED","DISABLED"))
    counts["RECOVERED"]=sum(bool(r.get("recovered")) for r in all_rs)
    # Seven-day overview uses persisted expectations, never hides MISSING windows.
    days=[(begin+timedelta(days=n)).strftime("%Y-%m-%d") for n in range(length)] if period!="day" else [(anchor-timedelta(days=n)).strftime("%Y-%m-%d") for n in range(6,-1,-1)]
    history=defaultdict(Counter)
    recovered=set()
    with db.transaction() as s:
        recovered={wid for wid in s.scalars(select(Attempt.window_id).join(Window,Window.id==Attempt.window_id).where(Window.period_start>=days[0]+'T00:00:00Z',Window.period_start<svc.iso(finish),Attempt.status.in_(['FAILED','TIMEOUT','WORKER_OFFLINE','UNKNOWN'])).distinct())}
        history_hosts={h.id:h for h in s.scalars(select(Host))}
        for w in s.scalars(select(Window).where(Window.period_start>=days[0]+"T00:00:00Z",Window.period_start<svc.iso(svc.dt(days[-1]+"T00:00:00Z")+timedelta(days=1)))):
            h=history_hosts[w.host_id]
            if f.get("scope") and h.scope!=f["scope"]:continue
            if f.get("host") and h.id!=f["host"] or f.get("zone") and h.zone!=f["zone"]:continue
            if f.get("host_group_id") and f["host_group_id"] not in w.snapshot.get("host_group_ids",[w.snapshot.get("host_group_id")]):continue
            if f.get("schedule_id") and w.snapshot.get("schedule_id")!=f["schedule_id"]:continue
            history[(w.collector_id,w.period_start[:10])][w.status]+=1
            if w.status=='SUCCESS' and w.id in recovered:history[(w.collector_id,w.period_start[:10])]['RECOVERED']+=1
    f["date"]=anchor.strftime("%Y-%m-%d")
    daily=defaultdict(Counter)
    for row in all_rs:daily[row["period_start"][:10]][row["status"]]+=1
    worker_rows=workers()
    listing=cs if view=="settings" and section=="collectors" or view=="collectors" and (overview or not f.get("collector_id")) else hosts() if view=="settings" else worker_rows if view=="workers" else sorted(rs,key=lambda r:(r["period_start"],r["host"]),reverse=True)
    if scope_tabs and f.get("scope"):listing=[c for c in listing if c["scope"]==f["scope"]]
    paginated=view in ("collectors","runs")
    pages=max(1,(len(listing)+page_size-1)//page_size) if paginated else 1;page=min(page,pages)
    visible=listing[(page-1)*page_size:page*page_size] if paginated else listing
    card_collectors=[c for c in cs if (not f.get("scope") or c["scope"]==f["scope"]) and (not f.get("collector_id") or c["id"]==f["collector_id"])]
    if view=='runs' and visible:
        with db.transaction() as session:
            details={r['id']:r for r in svc.rows(session,{},window_ids=[r['id'] for r in visible])}
            visible=[details[r['id']] for r in visible]
    context={"selected_hour":selected_hour,"overview":overview,"scope_tabs":scope_tabs,"scope_names":scope_names,"host_groups":host_groups(),"card_collectors":card_collectors,"period":period,"section":section,"page":page,"pages":pages,"page_size":page_size,"visible":visible,"daily":daily,"range_start":begin.strftime("%Y-%m-%d"),"range_end":(finish-timedelta(days=1)).strftime("%Y-%m-%d"),"previous_date":(begin-timedelta(days=1)).strftime("%Y-%m-%d"),"following_date":finish.strftime("%Y-%m-%d"),"request":request,"filters":f,"view":view,"collectors":cs,"rows":visible,"total":len(rs),"counts":counts,"expected":expected,
             "success_rate":round(counts["SUCCESS"]/expected*100,1) if expected else 0,"days":days,"history":history,
             "catalog":cat,"workers":worker_rows,"hosts":hosts(),"csrf":web_token(request),"today":svc.now().strftime("%Y-%m-%d"),"cadences":CADENCES}
    return templates.TemplateResponse(request=request,name="dashboard.html",context=context)
@app.get("/collections/{collection_id}")
def detail(request:Request,collection_id:str,page:int=Query(1,ge=1)):
    run=collection(collection_id)
    attempts=list(reversed(run["attempts"]))
    pages=max(1,(len(attempts)+24)//25);page=min(page,pages)
    return templates.TemplateResponse(request=request,name="detail.html",context={"request":request,"run":run,"attempts":attempts[(page-1)*25:page*25],"page":page,"pages":pages,"csrf":web_token(request)})
