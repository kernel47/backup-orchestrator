# DEV : une machine, deux dossiers indépendants

Le serveur Prefect et un worker process peuvent fonctionner sur la même machine, avec deux clones distincts du même dépôt. Chaque clone contient son `.env`, son `venv/` et son `.runtime/`. Aucun service systemd n'est nécessaire pour ce test. Sans worker, l'API et la UI fonctionnent, mais les deployments ne sont pas exécutés.

Exemple Linux : `/data/backup-orchestrator/server` et `/data/backup-orchestrator/worker`. Sur macOS ou une machine sans `/data`, utiliser deux dossiers dans votre espace DEV en adaptant les chemins ci-dessous.

## Installation

```bash
mkdir -p /data/backup-orchestrator
git clone git@github.com:kernel47/backup-orchestrator.git /data/backup-orchestrator/server
git clone git@github.com:kernel47/backup-orchestrator.git /data/backup-orchestrator/worker
bash /data/backup-orchestrator/server/scripts/install_server.sh
bash /data/backup-orchestrator/worker/scripts/install_worker.sh
```

Dans **server/.env**, configurer :

```dotenv
PREFECT_API_HOST=127.0.0.1
PREFECT_API_PORT=4200
PREFECT_SERVER_URL=http://127.0.0.1:4200
PREFECT_SERVER_ANALYTICS_ENABLED=false
PREFECT_DB_URL=
WORKER_CODE_PATH=/data/backup-orchestrator/worker
```

Conserver les horaires et paramètres du modèle serveur. La base reste dans `server/.runtime/prefect/prefect.db`.

Dans **worker/.env**, conserver le modèle worker et adapter au minimum :

```dotenv
ENVIRONMENT=development
REGION=EMEA
SATELLITE_NAME=dev-local
PREFECT_API_URL=http://127.0.0.1:4200/api
PREFECT_WORK_POOL=backup-emea-01
PREFECT_SERVER_ANALYTICS_ENABLED=false
```

Renseigner les vraies connexions de recette NetBackup et Backup Hub pour lancer une collecte. Le serveur n'a pas besoin de ces secrets.

## Exécution

Terminal 1 :

```bash
cd /data/backup-orchestrator/server
venv/bin/python -m scripts.run_service server
```

Ouvrir http://127.0.0.1:4200. Terminal 2 :

```bash
cd /data/backup-orchestrator/server
venv/bin/python -m server.register
cd /data/backup-orchestrator/worker
venv/bin/python -m scripts.run_service worker
```

Pour vérifier uniquement l'apparition du worker sans credentials NetBackup, remplacer sa dernière commande par :

```bash
mkdir -p .runtime/{prefect,tmp}
PREFECT_API_URL=http://127.0.0.1:4200/api \
PREFECT_HOME="$PWD/.runtime/prefect" \
TMPDIR="$PWD/.runtime/tmp" \
venv/bin/prefect worker start --pool backup-emea-01 --type process --name dev-local --limit 1
```

Ce worker prend uniquement les runs de `backup-emea-01`. Les cinq autres pools peuvent rester sans worker pour ce test. Les schedules sont actifs après registration : les mettre en pause dans la UI pour faire uniquement des runs manuels.

Déclencher `netbackup/emea-sat01-jobs` depuis la UI. Sans configuration worker complète, il échoue à la validation ; sans raccordement au module privé, il échoue à la collecte. Démarrer un worker ne raccorde pas ces intégrations.

Arrêter chaque processus avec Ctrl+C. Chaque rôle conserve ses propres données sous son `.runtime/`. Les tests unitaires ne nécessitent ni worker actif ni accès externe ; les lancer depuis le clone choisi :

```bash
venv/bin/pip install -r requirements-dev.txt
PREFECT_HOME="$PWD/.runtime/prefect" venv/bin/python -m pytest -q
```
