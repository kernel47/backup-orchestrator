"""Point unique de raccordement au module interne netbackup-py.

Chaque méthode doit retourner un itérable de dictionnaires, paginé à la source.
Ne jamais convertir toutes les pages en liste. Transmettre les timeouts et
verify_ssl au client réel. Traduire uniquement les erreurs temporaires vers
TemporaryCollectionError, sans message contenant une réponse ou un secret.
"""


class TemporaryCollectionError(RuntimeError):
    pass


class NetBackupCollector:
    def __init__(self, config):
        self.config = config
        # Construire ici le véritable client netbackup-py (interface inconnue).

    def collect_policies(self):
        raise NotImplementedError("Raccorder netbackup-py : policies")

    def collect_jobs(self, start_date, end_date):
        raise NotImplementedError("Raccorder netbackup-py : jobs")

    def collect_images(self, start_date, end_date):
        raise NotImplementedError("Raccorder netbackup-py : images")

    def collect_image_contents(self, image_id):
        raise NotImplementedError("Raccorder netbackup-py : image_contents")

    def collect_slp(self):
        raise NotImplementedError("Raccorder netbackup-py : slp")

    def collect_clients(self):
        raise NotImplementedError("Raccorder netbackup-py : clients")

    def collect(self, asset, start_date=None, end_date=None, image_id=None):
        match asset:
            case "policies": return self.collect_policies()
            case "jobs": return self.collect_jobs(start_date, end_date)
            case "images": return self.collect_images(start_date, end_date)
            case "image_contents": return self.collect_image_contents(image_id)
            case "slp": return self.collect_slp()
            case "clients": return self.collect_clients()
            case _: raise ValueError("Asset inconnu")
