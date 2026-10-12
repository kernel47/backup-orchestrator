# Installation serveur

Référence : Ubuntu 24.04 LTS, un processus serveur Prefect et six workers distants. Les commandes d'installation système utilisent sudo ; les commandes Python s'exécutent avec l'utilisateur dédié. Remplacer l'URL Bitbucket par votre dépôt ; préparer une clé SSH de lecture pour `backup`.

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git curl
sudo useradd --system --create-home --home-dir /var/lib/backup --shell /bin/bash backup
sudo install -d -o backup -g backup /opt/backup-orchestrator
sudo -u backup git clone git@bitbucket.org:VOTRE_ESPACE/backup-orchestrator.git /opt/backup-orchestrator
sudo -u backup bash /opt/backup-orchestrator/scripts/install_server.sh
sudo -u backup nano /opt/backup-orchestrator/.env
```

Le `.env` n'est pas un script shell ; ne pas le sourcer. Le lanceur Python le charge et mappe :

| Variable projet | Paramètre officiel |
| --- | --- |
| PREFECT_API_HOST | PREFECT_SERVER_API_HOST |
| PREFECT_API_PORT | PREFECT_SERVER_API_PORT |
| PREFECT_SERVER_URL | PREFECT_API_URL et PREFECT_SERVER_UI_API_URL, suffixés `/api` |
| PREFECT_DB_URL | PREFECT_SERVER_DATABASE_CONNECTION_URL |

`PREFECT_SERVER_URL` doit être joignable par les utilisateurs UI et les workers. Le port d'écoute est indépendant de l'URL publique. Protéger l'accès API par le réseau d'entreprise et HTTPS selon l'infrastructure existante ; ne pas exposer ce service sans contrôle d'accès à Internet.

## Base de données

Démo SQLite : laisser `PREFECT_DB_URL` vide. Prefect crée et migre la base au démarrage dans `/var/lib/backup/.prefect/prefect.db`.

Production avec six workers : utiliser PostgreSQL, tout en gardant **un seul processus API**. Six satellites n'impliquent pas `prefect server start --workers 6`. Le mode serveur multi-processus exige d'autres composants et sort des contraintes du projet.

Installation locale PostgreSQL (ou utiliser une base gérée par votre DBA) :

```bash
sudo apt-get install -y postgresql
sudo -u postgres createuser --pwprompt prefect
sudo -u postgres createdb --owner=prefect prefect
sudo -u backup nano /opt/backup-orchestrator/.env
```

Renseigner `PREFECT_DB_URL=postgresql+asyncpg://prefect:MOT_DE_PASSE_ENCODÉ@127.0.0.1:5432/prefect`. Encoder les caractères spéciaux du mot de passe pour une URL. Conserver la valeur exclusivement dans `.env`, mode 600. Prefect inclut le driver asyncpg.

Migration explicite, avant démarrage (le lancement normal migre aussi automatiquement) :

```bash
sudo -u backup /opt/backup-orchestrator/.venv/bin/python -c 'from workers.common.config import server_environment; import subprocess; server_environment("/opt/backup-orchestrator/.env"); subprocess.run(["/opt/backup-orchestrator/.venv/bin/prefect", "server", "database", "upgrade", "-y"], check=True)'
```

Ne jamais utiliser `database reset` en production. Prévoir la sauvegarde PostgreSQL et de la configuration via les procédures existantes.

## Service et registration

```bash
sudo cp /opt/backup-orchestrator/systemd/prefect-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prefect-server
sudo systemctl --no-pager status prefect-server
curl --fail http://prefect-server:4200/api/health
sudo journalctl -u prefect-server -n 100 --no-pager
cd /opt/backup-orchestrator
sudo -u backup .venv/bin/python -m server.register
```

Accéder à `http://prefect-server:4200` dans le navigateur. La registration crée les six pools `process` puis applique les 24 deployments. Elle charge le flow pour son schéma, sans exécuter la collecte et sans nécessiter les secrets NetBackup sur le serveur.

```bash
sudo systemctl restart prefect-server
sudo systemctl stop prefect-server
sudo journalctl -u prefect-server -f
```

La documentation des [commandes serveur officielles](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) décrit SQLite, PostgreSQL et les migrations.
