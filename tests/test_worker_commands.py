import asyncio
import sys

import pytest
from worker.commands import build_command
from worker.executor import execute


DEFINITION = {
    'command': ['backup-collector', 'collect'],
    'context_args': {'asset': 'host', 'start-date': 'period_start', 'end-date': 'period_end'},
    'parameter_args': {'scope': 'scope', 'type': 'type', 'debug': 'debug', 'dry-run': 'dry_run'},
    'required_parameters': ['scope', 'type'],
}


def test_command_arguments_preserve_target_and_do_not_execute_parameter_text():
    task = {'host': 'host-01', 'period_start': '2026-10-29T06:00:00Z',
            'period_end': '2026-10-29T07:00:00Z',
            'parameters': {'scope': 'netbackup', 'type': 'jobs; $(touch /tmp/no)',
                           'debug': True, 'dry_run': False, 'asset': 'other-host', 'command': 'evil'}}
    argv = build_command(DEFINITION, task)
    assert argv == ['backup-collector', 'collect', '--asset=host-01',
                    '--start-date=2026-10-29T06:00:00Z', '--end-date=2026-10-29T07:00:00Z',
                    '--scope=netbackup', '--type=jobs; $(touch /tmp/no)', '--debug']
    task['parameters'].pop('type')
    with pytest.raises(ValueError, match='Missing command parameter: type'):
        build_command(DEFINITION, task)


@pytest.mark.parametrize('mode,expected', [('success','SUCCESS'), ('failure','FAILURE'),
                                         ('contradiction','FAILURE'), ('invalid','UNKNOWN'),
                                         ('timeout','TIMEOUT')])
def test_command_execution_reports_logs_and_task_metadata(tmp_path, monkeypatch, mode, expected):
    script = tmp_path / 'external_collector.py'
    script.write_text('''import json,sys,time
print("Collection progress",file=sys.stderr)
mode=sys.argv[1]
if mode=="timeout":time.sleep(10)
if mode=="invalid":print("not json");sys.exit(0)
print(json.dumps({"status":"FAILURE" if mode=="failure" else "SUCCESS","summary":"Collected", "records_collected":12,"records_sent":12}))
sys.exit(2 if mode in ("failure","contradiction") else 0)
''')
    monkeypatch.setattr('worker.executor.load', lambda name: {'external': {'command': [sys.executable,str(script),mode]}})
    task = {'script':'external','timeout_seconds':1,'parameters':{},'host':'host-01',
            'period_start':'2026-10-29T06:00:00Z','period_end':'2026-10-29T07:00:00Z',
            'attempt_number':2,'task_id':'attempt-02'}
    report,logs = asyncio.run(execute(task))
    assert report['status']==expected
    assert report['host']=='host-01' and report['attempt_number']==2
    assert report['period_start']==task['period_start'] and report['period_end']==task['period_end']
    assert report['started_at'] and report['finished_at'] and report['collected_at']
    assert 'Collection progress' in logs
    if mode=='failure':assert report['summary']=='Collected'  # Keep valid failure reports on nonzero exit.


@pytest.mark.parametrize('status,expected', [('success','SUCCESS'), ('failure','FAILURE'), ('unknown','UNKNOWN'), ('timeout','TIMEOUT')])
def test_backup_collector_native_report(tmp_path, monkeypatch, status, expected):
    import json
    payload = {'schema_version':'1.0','collection_id':'external-01','status':status,
               'asset_id':'external-host','region':'EMEA','technology':'netbackup','resource':'jobs',
               'collection':{'status':'failure','collected':2906,'invalid':0,'excluded':0},
               'storage':{'status':'failure','destination':'console','created':0,'updated':0,'displayed':2906},
               'performance':{'cpu_user_seconds':3.1},'duration_seconds':17.454,
               'errors':[{'code':'EXAMPLE','message':'Diagnostic'}]}
    script=tmp_path/'native.py'
    script.write_text('import sys\nprint('+repr(json.dumps(payload))+')\nprint("native log",file=sys.stderr)\n')
    monkeypatch.setattr('worker.executor.load', lambda name: {'external': {
        'command':[sys.executable,str(script)], 'report_format':'backup_collector'}})
    result,logs=asyncio.run(execute({'script':'external','timeout_seconds':3,'host':'assigned-host',
                                   'task_id':'assigned-task','attempt_number':2,
                                   'period_start':'2026-10-29T06:00:00Z','period_end':'2026-10-29T07:00:00Z'}))
    # Only the external final status decides the business outcome, never nested fields.
    assert result['status']==expected
    assert result['details']['backup_collector']==payload
    assert result['host']=='assigned-host' and result['task_id']=='assigned-task'
    assert result['records_collected']==2906 and result['records_sent']==0
    assert result['duration_seconds']!=17.454
    assert 'native log' in logs


def test_backup_collector_invalid_status_is_rejected():
    from worker.reports import backup_collector_report
    for payload in ({}, {'status':'partial'}, [], {'status':True}):
        with pytest.raises(ValueError):backup_collector_report(payload)
