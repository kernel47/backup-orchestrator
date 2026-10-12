"""Enveloppe provisoire : le mapping métier dépend du contrat Backup Hub."""
import hashlib
import json
from datetime import date, datetime, timezone
from itertools import islice


def json_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Datetime source sans fuseau")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError("Type source non JSON : adapter le parser")


def parse_record(raw, master, asset):
    if not isinstance(raw, dict):
        raise TypeError("Le collector doit produire des dictionnaires")
    canonical = json.dumps(raw, default=json_value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity = json.dumps([master, asset, json.loads(canonical)], sort_keys=True, separators=(",", ":"))
    return {"id": hashlib.sha256(identity.encode()).hexdigest(), "data": json.loads(canonical)}


def batches(records, size):
    if size < 1:
        raise ValueError("Taille de lot invalide")
    iterator = iter(records)
    while batch := list(islice(iterator, size)):
        yield batch
