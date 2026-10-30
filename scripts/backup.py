"""Online SQLite snapshot with integrity checks and an optional restore drill."""
import argparse,os,sqlite3,tempfile
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy.engine import make_url
from orchestrator.config import settings


def backup(destination,check_restore=False):
    url=make_url(settings.database_url)
    if url.drivername!='sqlite' or not url.database:raise ValueError('This backup command supports a file-based SQLite master')
    source=Path(url.database).resolve();destination=Path(destination).resolve()
    if not source.exists() or source==destination or destination.exists():raise ValueError('Choose a new backup path, distinct from the live database')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Create privately before SQLite opens the file.
    descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(descriptor)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as live,sqlite3.connect(destination) as snapshot:live.backup(snapshot)
        verify(destination)
        if check_restore:
            with tempfile.TemporaryDirectory(prefix='tasker-restore-') as directory:
                restored=Path(directory)/'restored.db'
                with sqlite3.connect(destination) as src,sqlite3.connect(restored) as target:src.backup(target)
                verify(restored)
    except Exception:destination.unlink(missing_ok=True);raise
    return destination


def verify(path):
    with sqlite3.connect(path) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Backup contains foreign-key violations')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='var/backups/tasker-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.db')
    parser.add_argument('--check-restore',action='store_true',help='Restore into a temporary database and verify it without touching the master')
    args=parser.parse_args();print('Verified backup:',backup(args.output,args.check_restore))

if __name__=='__main__':main()
