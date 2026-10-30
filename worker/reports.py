"""Translate external reports without deciding their business outcome."""


def backup_collector_report(payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    status = payload.get('status')
    if not isinstance(status, str):
        raise ValueError('Missing final status')
    status = status.upper()
    if status == 'FAILED':
        status = 'FAILURE'
    if status not in ('SUCCESS', 'FAILURE', 'UNKNOWN', 'TIMEOUT'):
        raise ValueError('Unsupported final status')
    collection = payload.get('collection')
    collection = collection if isinstance(collection, dict) else {}
    report = {
        'status': status,
        'summary': payload.get('summary') or f'backup-collector reported {status}',
        'records_collected': payload.get('records_collected', collection.get('collected', 0)),
        # Do not infer delivery from displayed/created/updated/noop counters.
        'records_sent': payload.get('records_sent', 0),
        'error': payload.get('error'),
        'details': {'backup_collector': payload},
    }
    if payload.get('collected_at') is not None:
        report['collected_at'] = payload['collected_at']
    return report
