"""Install the master or worker as a Linux systemd service."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIRECTORY = Path('/etc/systemd/system')


def render_unit(role, root, user):
    root = Path(root).resolve()
    if role not in ('master', 'worker'):
        raise ValueError('Role must be master or worker')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*\$?', user) or user == 'root':
        raise ValueError('Choose a dedicated non-root service account, for example tasker')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(root)):
        raise ValueError('The installation path must not contain spaces or systemd special characters')
    return f'''[Unit]
Description=Tasker {role}
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={root}
ExecStart={root}/scripts/start_{role}.sh
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def install(role, root, user, start=False):
    unit = render_unit(role, root, user)
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        raise ValueError('Service installation requires Linux/systemd. On macOS use scripts/start_local.sh.')
    if os.geteuid() != 0:
        raise ValueError('Run this installer with sudo; the service itself runs as the specified user')
    import pwd
    try:
        pwd.getpwnam(user)
    except KeyError:
        raise ValueError(f'Service account {user} does not exist; create it and grant access to the project first') from None
    root = Path(root).resolve()
    for relative in ('.venv/bin/python', f'scripts/start_{role}.sh', '.env' if role == 'master' else '.env.worker'):
        if not (root / relative).is_file():
            raise ValueError(f'Missing {relative}; run install_{role}.sh and configure its environment first')
    name = f'tasker-{role}.service'
    target = UNIT_DIRECTORY / name
    descriptor, temporary = tempfile.mkstemp(prefix='.tasker-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(unit)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if start:
        subprocess.run(['systemctl', 'enable', '--now', name], check=True)
    print(f'Installed {target}; application files remain in {root}')
    print(f'Check: systemctl status {name}')
    print(f'Logs: journalctl -u {name} -n 100 --no-pager')
    if not start:
        print(f'Start and enable at boot: systemctl enable --now {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('master', 'worker'))
    parser.add_argument('--user', required=True, help='Existing non-root account that can access the project')
    parser.add_argument('--start', action='store_true', help='Also enable the service at boot and start it now')
    parser.add_argument('--print', dest='print_only', action='store_true', help='Print the unit without changing the machine')
    args = parser.parse_args()
    try:
        if args.print_only:
            print(render_unit(args.role, ROOT, args.user), end='')
        else:
            install(args.role, ROOT, args.user, args.start)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
