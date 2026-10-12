"""Transport HTTP ; confirmer l'enveloppe et l'acknowledgement avant production."""
import hashlib
import json

import httpx


class TemporaryUploadError(RuntimeError):
    pass


def upload_batch(config, asset, records):
    payload = {"schema_version": 1, "master": config.master, "asset": asset,
               "region": config.region, "satellite": config.satellite, "records": records}
    # La clé exclut le satellite afin de dédupliquer une reprise sur un autre hôte.
    identity = json.dumps([config.master, asset, sorted(r["id"] for r in records)], separators=(",", ":"))
    key = hashlib.sha256(identity.encode()).hexdigest()
    try:
        response = httpx.post(config.hub_url, json=payload,
                              headers={"Authorization": f"Bearer {config.hub_token}", "Idempotency-Key": key},
                              timeout=min(config.timeout, 120), follow_redirects=False)
    except httpx.TransportError:
        raise TemporaryUploadError("Backup Hub : erreur de transport") from None
    if response.status_code in {408, 429} or response.status_code >= 500:
        raise TemporaryUploadError(f"Backup Hub : HTTP {response.status_code}")
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Backup Hub : HTTP {response.status_code}")
    # Hypothèse de contrat : 2xx signifie tout le lot accepté durablement.
    return len(records)
