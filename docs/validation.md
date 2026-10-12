# Validation locale — 12 octobre 2026

Environnement de validation : Python 3.12.4, Prefect 3.8.8, macOS. Les services Linux sont fournis pour installation ; systemd et PostgreSQL n'ont pas été exécutés ici.

Vérifications réalisées :

- 31 tests pytest réussis, sans serveur NetBackup réel.
- `pip check` : aucune dépendance incompatible déclarée.
- Compilation Python et syntaxe des trois scripts Bash vérifiées.
- Serveur Prefect temporaire sur loopback avec SQLite et services natifs.
- Registration appliquée deux fois : exactement six pools process et 24 deployments.
- Entrypoint local et étapes `set_working_directory` lus via l'API et vérifiés.
- Scheduler natif : 72 runs automatiques observés après correction de dépendance.
- Run manuel pris par un vrai worker process, code chargé depuis le workspace, état Failed attendu pour l'adaptateur NetBackup non raccordé.
- Worker et serveur temporaires arrêtés après validation.

L'installation initiale a sélectionné SQLAlchemy 2.1.4, avec lequel le scheduler a échoué sur une insertion ORM. `requirements.txt` fixe SQLAlchemy 2.0.54 ; le scheduler a ensuite produit ses runs sans cette erreur. Conserver cette version avec la version Prefect validée, puis revalider lors d'une mise à jour.

Les identifiants employés dans le test worker étaient synthétiques ; aucun appel NetBackup ni livraison à Backup Hub n'a eu lieu. La recette d'intégration réelle reste conditionnée au raccordement du client privé et à la confirmation du contrat Hub.
