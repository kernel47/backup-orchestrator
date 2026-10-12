from datetime import datetime, timezone

ASSETS = {"policies", "jobs", "images", "image_contents", "slp", "clients"}


def parse_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Date invalide : ISO 8601 avec fuseau requis") from None
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("La date doit inclure un fuseau horaire")
    return date.astimezone(timezone.utc)


def validate_parameters(asset, start_date=None, end_date=None, image_id=None):
    if asset not in ASSETS:
        raise ValueError(f"Asset inconnu : {asset}")
    start, end = parse_date(start_date), parse_date(end_date)
    if start and end and start > end:
        raise ValueError("start_date doit précéder end_date")
    if asset not in {"jobs", "images"} and (start or end):
        raise ValueError("Les dates ne sont acceptées que pour jobs et images")
    if asset == "image_contents" and not image_id:
        raise ValueError("image_id obligatoire pour image_contents")
    if asset != "image_contents" and image_id is not None:
        raise ValueError("image_id réservé à image_contents")
    return start, end
