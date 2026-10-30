import argparse,asyncio,logging,os,signal,time
import httpx
from orchestrator.config import settings
from .executor import execute
from .timezone import worker_timezone
log=logging.getLogger("worker")
async def run(worker_id):
    spec={"concurrency":settings.worker_concurrency}
    token=settings.worker_token
    local_zone=worker_timezone(settings.worker_timezone)
    os.environ["TZ"]=local_zone
    if hasattr(time,"tzset"):time.tzset()
    stop=asyncio.Event();loop=asyncio.get_running_loop();active=set()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    async with httpx.AsyncClient(base_url=settings.master_url,headers={"Authorization":"Bearer "+token},timeout=15) as http:
        async def post(path,data):
            response=await http.post(path,json=data);response.raise_for_status();return response.json()
        body={"worker_id":worker_id}
        async def work(task):
            execution=asyncio.create_task(execute(task))
            try:
                while not execution.done():
                    done,_=await asyncio.wait([execution],timeout=max(.2,task.get("lease_seconds",settings.lease_seconds)/3))
                    if not done:await post(f"/api/tasks/{task['task_id']}/heartbeat",body)
                result,logs=await execution
                payload=dict(body,result=result,logs=logs)
                for _ in range(5):
                    try:await post(f"/api/tasks/{task['task_id']}/result",payload);break
                    except httpx.TransportError:await asyncio.sleep(1)
                log.info("%s %s %s",worker_id,task["task_id"],result["status"])
            except Exception:log.exception("Attempt failed")
            finally:
                if not execution.done():execution.cancel()
                await asyncio.gather(execution,return_exceptions=True)
        try:
            while not stop.is_set():
                try:
                    await post("/api/workers/heartbeat",dict(body,timezone=local_zone))
                    active={t for t in active if not t.done()}
                    if len(active)<spec["concurrency"]:
                        task=await post("/api/workers/claim",body)
                        if task:active.add(asyncio.create_task(work(task)))
                except httpx.HTTPError:log.warning("Master unavailable",exc_info=True)
                try:await asyncio.wait_for(stop.wait(),timeout=settings.poll_seconds)
                except asyncio.TimeoutError:pass
        finally:
            for task in active:task.cancel()
            await asyncio.gather(*active,return_exceptions=True)
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--id",default=settings.worker_id);args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    if not args.id:parser.error("Set WORKER_ID in .env or pass --id")
    asyncio.run(run(args.id))
if __name__=="__main__":main()
