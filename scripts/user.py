"""Create/update a named dashboard account. Passwords are prompted, never echoed."""

import argparse
import getpass
import re

from sqlalchemy import select

from orchestrator import db
from orchestrator.config import settings
from orchestrator.models import User, Zone
from orchestrator.security import password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument(
        "--role", choices=("admin", "operator", "viewer"), default="viewer"
    )
    parser.add_argument(
        "--zones", required=True, help="Comma-separated allowed zone IDs"
    )
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.username)
        or args.username == settings.auth_username
    ):
        parser.error("Use a unique username, different from the .env administrator")
    db.init()
    with db.Session.begin() as session:
        zones = list(
            dict.fromkeys(z.strip() for z in args.zones.split(",") if z.strip())
        )
        if not zones or set(zones) - set(session.scalars(select(Zone.id))):
            parser.error("Choose existing zones")
        user = session.get(User, args.username)
        if args.disable:
            if not user:
                parser.error("Unknown user")
            user.enabled = False
            print("Account disabled; existing sessions are invalid.")
            return
        password = getpass.getpass("Password (12 characters minimum): ")
        if len(password) < 12 or password != getpass.getpass("Confirm password: "):
            parser.error("Passwords must match and contain at least 12 characters")
        if not user:
            user = User(username=args.username)
            session.add(user)
        user.password_hash = password_hash(password)
        user.zones = zones
        user.role = args.role
        user.enabled = True
    print("Account saved; permissions apply to every request.")


if __name__ == "__main__":
    main()
