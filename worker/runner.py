"""One script per subprocess. stdout is reserved for the validated result JSON."""
import contextlib,importlib.util,json,sys
from orchestrator.config import ROOT,load
from orchestrator.schemas import CollectorResult

def main():
    context=json.loads(sys.stdin.read());alias=sys.argv[1]
    catalog=load("worker_scripts.yaml")
    if alias not in catalog:raise ValueError("Script is not allowed on this satellite")
    path=(ROOT/catalog[alias]).resolve()
    if not path.is_relative_to((ROOT/"collectors").resolve()) or not path.is_file():raise ValueError("Script outside collectors directory")
    with contextlib.redirect_stdout(sys.stderr):
        spec=importlib.util.spec_from_file_location("user_collector",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        report=module.collect(context)
        try:result=CollectorResult.model_validate(report)
        except ValueError:raise ValueError("Collector returned an invalid execution report") from None
    print(result.model_dump_json())
if __name__=="__main__":main()
