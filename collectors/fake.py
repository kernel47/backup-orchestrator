"""Generic fake collector for testing real execution, results and retries."""
import hashlib,time
from datetime import datetime,timezone

def collect(context):
    started=time.monotonic()
    parameters=context["parameters"]
    print(f"DEMO {context['collector']} | host={context['host']} | period={context['period_start']} → {context['period_end']}")
    time.sleep(float(parameters.get("delay_seconds",1.2)))
    simulation=parameters.get("simulation","SUCCESS")
    if simulation=="TIMEOUT":time.sleep(context["timeout_seconds"]+5)
    if simulation=="ERROR":raise RuntimeError("Demo exception")
    count=int(hashlib.sha256((context["host"]+context["period_start"]+context["collector_id"]).encode()).hexdigest()[:6],16)%1400+20
    return {"status":"FAILURE" if simulation in ("FAILED","FAILURE") else "UNKNOWN" if simulation=="UNKNOWN" else "SUCCESS",
            "summary":"Simulated failure" if simulation in ("FAILED","FAILURE") else "Outcome could not be confirmed" if simulation=="UNKNOWN" else f"Simulated collection and delivery of {count} records",
            "collected_at":datetime.now(timezone.utc).isoformat(),
            "duration_seconds":round(time.monotonic()-started,3),
            "records_collected":count,"records_sent":0 if simulation in ("FAILED","FAILURE","UNKNOWN") else count,
            "error":"Simulated delivery failure" if simulation in ("FAILED","FAILURE") else None,"details":{"simulated":True,"collection":"Completed","delivery":"Failed" if simulation in ("FAILED","FAILURE") else "Unknown" if simulation=="UNKNOWN" else "Completed","reference":None}}
