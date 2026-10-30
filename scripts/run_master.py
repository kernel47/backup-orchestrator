import uvicorn
from orchestrator.config import settings
if __name__=='__main__':
    uvicorn.run('orchestrator.main:app',host=settings.master_host,port=settings.master_port,workers=1)
