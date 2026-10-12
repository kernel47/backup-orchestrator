# Raccordements restant à réaliser

## netbackup-py

Le nom de distribution ne révèle ni le nom du module importable ni ses méthodes. Dans `workers/collectors/netbackup.py`, construire le client interne avec username/password/master/verify_ssl/timeout, puis raccorder les six méthodes. Ne pas réécrire ses appels HTTP.

Chaque méthode retourne un itérable de dictionnaires bruts. Paginer avec les fonctions natives du module et émettre les records au fil des pages ; le batch aval ne limite pas la mémoire si le client charge déjà toute la collecte. Fermer les sessions et ressources via un context manager ou `try/finally` dans les itérateurs. Respecter les bornes UTC fournies (éventuellement absentes), et préciser inclusivité, date utilisée et portée par défaut.

Convertir les seules erreurs temporaires connues en `TemporaryCollectionError`, sans inclure credentials, URL authentifiée, réponse source ou payload. Les erreurs permanentes remontent immédiatement. Le flow nettoie leur message avant exposition à Prefect. Les timeouts et erreurs de l'itérateur doivent également être traduits par l'adaptateur.

Ajouter des tests de mapping avec fixtures synthétiques pour le client interne lorsque son contrat sera disponible. Éviter les logs DEBUG du client qui pourraient contenir des secrets.

## Backup Hub

Le transport proposé POSTe vers `BACKUP_HUB_URL` avec Bearer et `Idempotency-Key`. Enveloppe actuelle :

```json
{"schema_version":1,"master":"MASTER","asset":"jobs","region":"EMEA","satellite":"emea-sat01","records":[{"id":"SHA256","data":{}}]}
```

Cet exemple décrit un schéma, sans prétendre fournir des données NetBackup. Confirmer le schéma, l'endpoint, l'authentification et la limite de taille avec l'équipe Hub. Le parser actuel conserve les dictionnaires bruts après normalisation JSON ; remplacer ce mapping par les champs autorisés avant la première livraison réelle.

Le transport considère tout 2xx comme l'acceptation durable de **tout** le lot. Si le Hub répond 202 avec traitement ultérieur, ou accepte partiellement un lot, implémenter son véritable acknowledgement avant production. Aucun résultat partiel n'est actuellement interprété.

ID actuel : hash de master + asset + contenu canonique. Il permet de dédupliquer un record inchangé malgré un nouveau run ou satellite. Un changement de contenu crée un nouvel ID ; ce n'est pas une clé d'upsert métier. Dès que disponibles, préférer les identifiants métier et versions spécifiques à chaque asset, notamment jobs/images et contenus d'image. Le Hub doit dédupliquer **chaque record** et rendre l'acceptation atomique ou rapporter les rejets. Une clé HTTP de lot ne garantit pas à elle seule une livraison exactly-once.

Le transport ne journalise pas les corps, tokens ou URLs de requêtes. 408/429/5xx et erreurs de transport sont temporaires ; les autres réponses non 2xx sont permanentes. Pas de retry HTTP interne : Prefect pilote les tentatives.
