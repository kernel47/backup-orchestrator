# Installation satellite

Référence Ubuntu 24.04, utilisateur `backup`, Python 3.12, clone identique dans `/opt/backup-orchestrator`. Préparer l'accès SSH Bitbucket du compte `backup`.

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git curl
sudo useradd --system --create-home --home-dir /var/lib/backup --shell /bin/bash backup
sudo install -d -o backup -g backup /opt/backup-orchestrator
sudo -u backup git clone git@bitbucket.org:VOTRE_ESPACE/backup-orchestrator.git /opt/backup-orchestrator
sudo -u backup bash /opt/backup-orchestrator/scripts/install_worker.sh
# Remplacer par le véritable artefact interne, fourni par votre équipe.
sudo -u backup /opt/backup-orchestrator/.venv/bin/pip install /CHEMIN/INTERNE/netbackup_py-VERSION-py3-none-any.whl
sudo -u backup nano /opt/backup-orchestrator/.env
sudo chmod 600 /opt/backup-orchestrator/.env
```

L'installation du wheel ne raccorde pas à elle seule l'adaptateur : suivre [integrations.md](integrations.md) et vérifier ses imports/méthodes réels. Tous les satellites doivent utiliser la même version validée du projet et du module interne.

| Région | Satellite | Pool |
| --- | --- | --- |
| EMEA | emea-sat01 | backup-emea-01 |
| EMEA | emea-sat02 | backup-emea-02 |
| APAC | apac-sat01 | backup-apac-01 |
| APAC | apac-sat02 | backup-apac-02 |
| AMER | amer-sat01 | backup-amer-01 |
| AMER | amer-sat02 | backup-amer-02 |

Adapter les trois variables correspondantes, l'URL Prefect, le master et les secrets. `BACKUP_HUB_URL` est l'endpoint POST complet. Le lancement valide la configuration avant de démarrer le worker. Le `.env` est chargé par Python, pas par `source` ou `EnvironmentFile` systemd.

## Connectivité

```bash
curl --fail http://prefect-server:4200/api/health
# Test TLS/HTTP uniquement : remplacer les deux URLs, sans secrets dans la commande.
curl --connect-timeout 10 --max-time 30 --head https://NETBACKUP_MASTER:1556/
curl --connect-timeout 10 --max-time 30 --head https://BACKUP_HUB_HOST/
```

401/403/405 prouvent éventuellement que le serveur HTTP répond ; ils ne prouvent ni l'authentification ni les droits de collecte. Pour un certificat interne, installer la CA de l'entreprise. Éviter de désactiver la vérification SSL en production. La validation fonctionnelle NetBackup exige un appel en lecture avec le client interne raccordé ; aucune commande API privée n'est inventée ici. La validation Hub exige un lot de recette et vérification de l'acceptation côté Hub.

## Service

Créer les pools depuis le serveur avant de démarrer les satellites.

```bash
sudo cp /opt/backup-orchestrator/systemd/prefect-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prefect-worker
sudo systemctl --no-pager status prefect-worker
sudo journalctl -u prefect-worker -n 100 --no-pager
```

Dans Prefect UI : Work Pools → pool du satellite → Workers. Son nom doit correspondre à `SATELLITE_NAME`. Un worker accepte un run simultané (`--limit 1`) ; ajuster explicitement le lanceur si nécessaire. Il hérite du virtualenv et de la configuration locale. Il n'héberge aucune base ni serveur Prefect.

Premier run : Deployments → `netbackup/emea-sat01-jobs` → Run → paramètres asset/dates. Sans raccordement NetBackup, le run sera Failed et indiquera `NotImplementedError`. Après raccordement, vérifier compteurs et livraison Hub.

```bash
sudo systemctl restart prefect-worker
sudo systemctl stop prefect-worker
sudo journalctl -u prefect-worker -f
```

## Mise à jour manuelle

Mettre le pool en pause dans la UI pour empêcher de nouveaux départs ; attendre que ses runs Running soient terminés, y compris ceux préchargés. Le scheduler peut continuer à créer des runs Scheduled. Ne pas modifier le code pendant une collecte. Ensuite :

```bash
cd /opt/backup-orchestrator
sudo -u backup bash scripts/update_worker.sh TAG_OU_COMMIT_VALIDÉ
```

Le compte `backup` doit pouvoir appeler sudo pour stop/start/status du service ; sinon effectuer les étapes avec un administrateur : arrêter le service, `git fetch --tags origin`, `git checkout --detach TAG_VALIDÉ`, réinstaller les requirements et le package, redémarrer, vérifier. Si l'installation échoue, laisser le worker arrêté, corriger ou revenir au tag précédent. Réactiver le pool après vérification. Ne pas copier un `.env` d'un autre satellite.
