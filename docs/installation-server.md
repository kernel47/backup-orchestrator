# Installation serveur

Référence : Ubuntu 24.04 LTS, un processus serveur Prefect et six workers distants. Les commandes d'installation système utilisent sudo ; les commandes Python s'exécutent avec l'utilisateur dédié. Remplacer l'URL Bitbucket par votre dépôt ; préparer une clé SSH de lecture pour le compte exécutant le clone.

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git curl
sudo useradd --system --no-create-home --home-dir /data/backup-orchestrator/server/.runtime/home --shell /bin/bash backup
sudo install -d -o backup -g backup /data/backup-orchestrator/server
sudo git clone git@bitbucket.org:VOTRE_ESPACE/backup-orchestrator.git /data/backup-orchestrator/server
sudo chown -R backup:backup /data/backup-orchestrator/server
sudo -u backup bash /data/backup-orchestrator/server/scripts/install_server.sh
sudo -u backup nano /data/backup-orchestrator/server/.env
```

Le `.env` n'est pas un script shell ; ne pas le sourcer. Le lanceur Python le charge et mappe :

| Variable projet | Paramètre officiel |
| --- | --- |
| PREFECT_API_HOST | PREFECT_SERVER_API_HOST |
| PREFECT_API_PORT | PREFECT_SERVER_API_PORT |
| PREFECT_SERVER_URL | PREFECT_API_URL et PREFECT_SERVER_UI_API_URL, suffixés `/api` |
| PREFECT_DB_URL | PREFECT_SERVER_DATABASE_CONNECTION_URL |

`PREFECT_SERVER_URL` doit être joignable par les utilisateurs UI et les workers. Le port d'écoute est indépendant de l'URL publique. Protéger l'accès API par le réseau d'entreprise et HTTPS selon l'infrastructure existante ; ne pas exposer ce service sans contrôle d'accès à Internet.

## Données et base locale

Laisser `PREFECT_DB_URL` vide : le lanceur fixe explicitement la base SQLite à `/data/backup-orchestrator/server/.runtime/prefect/prefect.db`. Aucun fichier n'est créé dans `~/.prefect` ou `/var/lib`. Si un chemin SQLite est configuré, il doit rester dans le clone ; une base extérieure est refusée. Le serveur garde un seul processus API, avec six workers distants.

Les migrations sont appliquées automatiquement au démarrage. Migration explicite avant démarrage :

```bash
cd /data/backup-orchestrator/server
sudo -u backup venv/bin/python -c 'from workers.common.config import server_environment; import subprocess; server_environment(); subprocess.run(["venv/bin/prefect", "server", "database", "upgrade", "-y"], check=True)'
```

Ne jamais utiliser `database reset` en production. Pour sauvegarder SQLite, arrêter le serveur avant de copier la base, ou employer l'API de sauvegarde SQLite. Sauvegarder également `.env` et le dossier `.runtime`. Cette configuration privilégie le stockage entièrement local demandé ; si la charge dépasse les capacités SQLite, une migration PostgreSQL et son stockage devront faire l'objet d'une configuration distincte.

## Service et registration

```bash
cd /data/backup-orchestrator/server
sudo -u backup bash scripts/install_service.sh server
sudo systemctl --no-pager status prefect-server
curl --fail http://prefect-server:4200/api/health
sudo journalctl -u prefect-server -n 100 --no-pager
cd /data/backup-orchestrator/server
sudo -u backup venv/bin/python -m server.register
```

Accéder à `http://prefect-server:4200` dans le navigateur. La registration crée les six pools `process` puis applique les 24 deployments. Elle charge le flow pour son schéma, sans exécuter la collecte et sans nécessiter les secrets NetBackup sur le serveur.

```bash
sudo systemctl restart prefect-server
sudo systemctl stop prefect-server
sudo journalctl -u prefect-server -f
```

La documentation des [commandes serveur officielles](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) décrit SQLite, PostgreSQL et les migrations.

Les logs applicatifs sont accessibles localement :

```bash
tail -n 100 /data/backup-orchestrator/server/.runtime/logs/prefect-server.log
```

`journalctl -u` reste utile pour les erreurs de démarrage systemd. Pour un autre dossier, remplacer `/data/backup-orchestrator/server` dans les commandes ; le script d'installation du service génère automatiquement les chemins de l'unité.
