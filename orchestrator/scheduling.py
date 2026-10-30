"""Worker-local calendar boundaries; persisted timestamps always use UTC."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

def valid_timezone(name):
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError('Use an IANA timezone, for example Europe/Paris or Asia/Singapore')
    return name

def boundary(day, clock, timezone_name):
    zone = ZoneInfo(timezone_name)
    hour, minute = map(int, clock.split(':'))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
    # A nonexistent spring-forward time moves forward by the DST gap.
    # An ambiguous fall-back time uses the first occurrence, once per local day.
    return local.astimezone(UTC)

def daily_period(day, clock, timezone_name):
    return boundary(day-timedelta(days=1), clock, timezone_name), boundary(day, clock, timezone_name)

def latest_daily_period(current, clock, timezone_name):
    day = current.astimezone(ZoneInfo(timezone_name)).date()
    if boundary(day, clock, timezone_name) > current:
        day -= timedelta(days=1)
    return daily_period(day, clock, timezone_name)

def next_daily(current, clock, timezone_name):
    day = current.astimezone(ZoneInfo(timezone_name)).date()
    due = boundary(day, clock, timezone_name)
    return due if due > current else boundary(day+timedelta(days=1), clock, timezone_name)

def local_label(value, timezone_name):
    if not timezone_name:
        return 'Timezone not reported'
    if isinstance(value,(float,int)):
        value=datetime.fromtimestamp(value,UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.astimezone(ZoneInfo(timezone_name)).strftime('%Y-%m-%d %H:%M:%S %Z')
