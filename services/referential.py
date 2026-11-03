"""Adapt the API request and field mapping here for your host referential.

The default contract is a complete JSON list of objects with hostname, region,
type and optional address/parameters/worker fields. Fetch every page before
returning records; an HTTP or validation failure must never become an empty list.
"""

import os

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orchestrator.config import ROOT
from orchestrator.inventory_service import HostRecord

# Adapt this mapping if your referential does not provide an explicit scope.
TYPE_SCOPES = {
    "master server": "NetBackup",
    "data domain": "DataDomain",
    "tape library": "TapeLibrary",
}


class ReferentialSettings(BaseSettings):
    url: str = ""
    token: str = Field(default="", repr=False)
    verify_ssl: bool = True
    model_config = SettingsConfigDict(
        env_prefix="REFERENTIAL_",
        env_file=os.environ.get("ORCHESTRATOR_ENV_FILE", str(ROOT / ".env")),
        extra="ignore",
    )


class ReferentialService:
    def __init__(self, config=None):
        self.config = config or ReferentialSettings()

    def fetch_hosts(self, region=None):
        """Adapt endpoint, authentication, response shape and pagination here."""
        if not self.config.url:
            raise ValueError(
                "Set REFERENTIAL_URL in the master .env before refreshing."
            )
        headers = {"Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = "Bearer " + self.config.token
        with httpx.Client(
            verify=self.config.verify_ssl,
            timeout=30,
            follow_redirects=True,
            max_redirects=10,
        ) as client:
            response = client.get(
                self.config.url,
                params={"region": region} if region else {},
                headers=headers,
            )
            response.raise_for_status()
            records = response.json()
        if not isinstance(records, list):
            raise ValueError(
                "Adapt fetch_hosts() to return the complete host list, including all pages."
            )
        return records

    def map_host(self, row):
        """Adapt referential fields to the orchestrator inventory contract here."""
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("hostname", "region", "type")
        ):
            raise ValueError(
                "Adapt map_host(): each host needs hostname, region and type."
            )
        parameters = row.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("Referential host parameters must be an object.")
        return HostRecord(
            id=row["hostname"],
            zone=row["region"].strip(),
            scope=row.get("scope")
            or TYPE_SCOPES.get(row["type"].strip().casefold(), "General"),
            preferred_worker=row.get("preferred_worker", ""),
            fallback_worker=row.get("fallback_worker", ""),
            parameters={
                **parameters,
                "address": row.get("address") or row["hostname"],
                "asset_type": row["type"],
            },
        ).model_dump()

    def collect_hosts(self, region=None):
        """Return every host type; optional region restricts a single-zone refresh."""
        if region is not None and (not isinstance(region, str) or not region.strip()):
            raise ValueError("Region must be a non-empty string.")
        region = region.strip() if region else None
        hosts = []
        for row in self.fetch_hosts(region):
            host = self.map_host(row)
            if region is None or host["zone"].casefold() == region.casefold():
                if region:
                    host["zone"] = region
                hosts.append(host)
        return hosts


def collect_hosts(region=None):
    return ReferentialService().collect_hosts(region)
