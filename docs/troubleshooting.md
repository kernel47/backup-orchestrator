# Dépannage

| Symptôme | Vérification |
| --- | --- |
| Worker absent/offline | service, URL `/api`, DNS, port, pool cible et journald |
| Run Late/Scheduled | pool en pause, worker arrêté, limite de concurrence ou horaire/fuseau |
| Configuration absente | `.env` dans le clone, propriétaire backup, permission 600 |
| NotImplementedError | raccorder les méthodes netbackup-py ; installer le wheel seul ne suffit pas |
| ModuleNotFoundError | virtualenv, installation editable, chemin absolu commun et version du wheel interne |
| Erreur à l'étape parsing | dictionnaires JSON, dates avec fuseau, mapping de contrat |
| Erreur upload HTTP 401/403 | token/droits Hub ; ne pas afficher le token |
| HTTP 429/5xx | retries Prefect, disponibilité Hub ; vérifier la déduplication des lots déjà reçus |
| SQLite locked | réduire la concurrence et conserver un processus serveur ; réévaluer le stockage si nécessaire |
| Timeout | réseau, API NetBackup, volume/pages et COLLECT_TIMEOUT ; il s'applique par tentative |

```bash
sudo systemctl --no-pager status prefect-server
sudo journalctl -u prefect-server -n 100 --no-pager
sudo systemctl --no-pager status prefect-worker
sudo journalctl -u prefect-worker -n 100 --no-pager
curl --fail http://prefect-server:4200/api/health
```

Dans Prefect UI, consulter état, durée, logs du flow et tâche. Le contexte master/asset/région/satellite et l'étape figurent dans l'erreur. Les erreurs du client sont volontairement réduites à leur type pour ne pas exposer de secrets ; diagnostiquer le client en environnement contrôlé si davantage de détail est nécessaire.

Après une panne Hub avec livraison partielle, relancer les mêmes paramètres seulement après validation de son contrat de déduplication. Après une mise à jour échouée, le service reste arrêté : remettre une version validée et ses dépendances, vérifier, puis démarrer et réactiver le pool.

Les logs applicatifs sont accessibles localement :

```bash
tail -n 100 /data/backup-orchestrator/server/.runtime/logs/prefect-server.log
tail -n 100 /data/backup-orchestrator/worker/.runtime/logs/prefect-worker.log
```

`journalctl -u` reste utile pour les erreurs de démarrage systemd. Pour un autre dossier, remplacer `/data/backup-orchestrator` dans les commandes ; le script d'installation du service génère automatiquement les chemins de l'unité.
