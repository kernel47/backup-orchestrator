# Data structure

The database is authoritative for all managed objects; see the root README for setup and source layout.

- A zone contains hosts and satellites; each belongs to exactly one zone.
- A scope groups compatible collectors and hosts.
- A host can belong to several host groups and be targeted by several collectors.
- A schedule binds one collector to one or more groups, optional zones, frequency and parameters.
- Execution is per host, collector and collection period, using an eligible worker in the host zone.
- Results can be inspected by zone, scope, group, collector and host. Retrying preserves history and the requested period.

External files are limited to .env runtime settings, the host discovery export and config/worker_scripts.yaml executable allowlist. No object YAML importer exists. Test fixtures are isolated under tests/ and do not configure the running application.
