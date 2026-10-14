# Deployments et planifications

La registration lit le `.env` serveur puis génère une configuration YAML native Prefect, crée les pools absents et appelle `prefect deploy --all --prefect-file ... --no-prompt`. Les noms stables `netbackup/<satellite>-<asset>` permettent la mise à jour sans duplication. Elle refuse un pool existant de type différent. Les pools existants conservent leurs réglages. Les deployments supprimés de la liste doivent être supprimés explicitement dans la UI.

```bash
cd /data/backup-orchestrator/server
sudo -u backup venv/bin/python -m server.register
```

Les horaires configurables dans `.env` : jobs `0 * * * *`, policies `0 2 * * *`, images `0 3 * * *`, slp `0 4 * * *`, timezone UTC par défaut. `DEPLOYMENT_CONCURRENCY=1` limite chaque deployment ; `--limit 1` limite l'ensemble des runs par worker. La stratégie native par défaut met en attente les runs excédentaires.

Chaque satellite possède quatre exemples, soit 24 deployments. **Ils sont tous actifs après registration.** Adapter les paramètres, les périmètres et éventuellement mettre les schedules en pause avant de raccorder les systèmes. Si deux satellites interrogent le même master, répartir leur périmètre ou valider la déduplication Hub.

## Code préinstallé

Le `pull` utilise uniquement `prefect.deployments.steps.set_working_directory`, avec `WORKER_CODE_PATH=/data/backup-orchestrator/worker`. Il change le répertoire du subprocess sur le satellite ; il ne télécharge pas Git. L'entrypoint est `workers/flows/netbackup.py:netbackup_flow`. La job variable `working_dir` est le même chemin. Ce dossier et `venv` doivent être installés et lisibles par `backup` sur tous les satellites. Le code central sert à inspecter le schéma, pas à collecter. Le package est installé en editable pour rendre les imports disponibles depuis les subprocess.

`prefect.yaml` est un exemple unitaire au chemin par défaut. La registration complète est la source de référence. Ne pas utiliser simultanément cet exemple et une configuration centrale différente pour le même deployment.

## Paramètres et relance

Les dates sont des chaînes ISO 8601 avec fuseau, normalisées en UTC ; une date sans heure/fuseau est refusée. `start_date <= end_date`. Seuls jobs/images acceptent les dates ; image_contents exige image_id.

```bash
# Exécuter avec PREFECT_API_URL configuré dans l'environnement de la CLI.
export PREFECT_API_URL=http://prefect-server:4200/api
/data/backup-orchestrator/server/venv/bin/prefect deployment run netbackup/emea-sat01-jobs --params '{"asset":"jobs","start_date":"2026-10-11T00:00:00Z","end_date":"2026-10-12T00:00:00Z"}'
```

Depuis UI, le même formulaire permet une période, une borne unique ou une relance avec les mêmes paramètres. Policies/clients/slp collectent sans dates. Pour image_contents, changer asset et fournir image_id dans le formulaire ou dans `--params`.

La tâche de pipeline a `retries=2`, soit **trois tentatives au total**, espacées de 60 secondes, uniquement pour les exceptions temporaires de l'adaptateur et du transport. Pas de retry pour une erreur de contrat, validation ou une méthode non raccordée. Le timeout `COLLECT_TIMEOUT` porte sur chaque tentative, pas sur la durée cumulée avec retries. Le client NetBackup réel doit également respecter ce timeout pour ses appels et pages ; un timeout Prefect n'est pas un mécanisme de retry du client.

Une tentative peut reprendre depuis le début après avoir transmis plusieurs lots. Les IDs de records sont indépendants du run et du satellite ; le Hub doit les dédupliquer. Les clés de lot seules ne suffisent pas si les pages changent entre deux lectures. Voir le contrat d'intégration.

Les états/logs/durées sont visibles dans UI ; les statistiques sont un petit retour de flow non persisté comme résultat de données. Les logs finaux contiennent région, satellite, master, asset et compteurs. Aucun jeu de données ni secret n'est retourné.

Référence : [stockage local officiel](https://docs.prefect.io/v3/how-to-guides/deployments/store-flow-code).

Le chemin `WORKER_CODE_PATH` désigne le clone worker, jamais le dossier serveur. Chaque rôle possède son `.env`, son `venv` et son `.runtime` indépendants.
