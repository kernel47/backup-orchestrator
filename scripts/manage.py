"""Local project-only process lifecycle, with PID identity checks."""
import json,os,signal,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
STATE=ROOT/'var/processes.json'
def identity(pid):return subprocess.run(['ps','-p',str(pid),'-o','lstart=,command='],capture_output=True,text=True).stdout.strip()
def stop():
    if not STATE.exists():return
    owned=[r for r in json.loads(STATE.read_text()) if r['identity'] and identity(r['pid'])==r['identity']]
    for r in reversed(owned):
        try:os.killpg(r['pid'],signal.SIGTERM)
        except ProcessLookupError:pass
    deadline=time.time()+12
    while time.time()<deadline and any(identity(r['pid'])==r['identity'] for r in owned):time.sleep(.2)
    for r in owned:
        if identity(r['pid'])==r['identity']:
            try:os.killpg(r['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    STATE.unlink(missing_ok=True)
def ready(url,timeout=30):
    deadline=time.time()+timeout
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status==200:return
        except Exception:time.sleep(.3)
    raise RuntimeError('Service unavailable: '+url)
def start():
    if STATE.exists() and any(identity(r['pid'])==r['identity'] for r in json.loads(STATE.read_text())):raise RuntimeError('Already running; use stop_local.sh')
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(('127.0.0.1',8000))
        except OSError:raise RuntimeError('Port 8000 is already in use')
    (ROOT/'var/logs').mkdir(parents=True,exist_ok=True);records=[];binary=ROOT/'.venv/bin/python'
    def launch(name,args):
        with open(ROOT/f'var/logs/{name}.log','a') as log:p=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,PYTHONUNBUFFERED='1'))
        time.sleep(.05);records.append({'name':name,'pid':p.pid,'identity':identity(p.pid)});STATE.write_text(json.dumps(records,indent=2))
    try:
        launch('master',[str(binary),'-m','uvicorn','orchestrator.main:app','--host','127.0.0.1','--port','8000'])
        ready('http://127.0.0.1:8000/health')
        if any(not identity(r['pid']) for r in records):raise RuntimeError('A process exited; see var/logs')
        print('Tasker : http://localhost:8000\nAPI Swagger : http://localhost:8000/docs\nWorkers : http://localhost:8000/?view=workers\nSign in: AUTH_USERNAME and AUTH_PASSWORD from .env')
    except BaseException:stop();raise
if __name__=='__main__':stop() if sys.argv[1]=='stop' else start()
