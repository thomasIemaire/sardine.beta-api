"""
Construction de filtres MongoDB et tri pour les endpoints de listing.
Utilisé par les services agents et flows.
"""

import re
from datetime import datetime

from beanie import PydanticObjectId
from bson.errors import InvalidId

from app.core.exceptions import BadRequestError


def build_filters(
    *,
    search: str | None = None,
    creator: str | None = None,
    origin: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    status: str | None = None,
    valid_statuses: set[str] | None = None,
) -> dict:
    """
    Construit un dictionnaire de filtres MongoDB à partir des query params.
    Lève BadRequestError (400) si un paramètre est invalide.
    """
    filters: dict = {}

    # search : ILIKE sur name et description
    if search:
        pattern = re.escape(search)
        regex = {"$regex": pattern, "$options": "i"}
        filters["$or"] = [{"name": regex}, {"description": regex}]

    # creator : liste d'IDs séparés par virgule (ignore les UUIDs invalides)
    if creator:
        creator_ids = []
        for c in creator.split(","):
            c = c.strip()
            if not c:
                continue
            try:
                creator_ids.append(PydanticObjectId(c))
            except (InvalidId, Exception):
                pass  # Ignorer silencieusement les IDs invalides
        if creator_ids:
            filters["created_by"] = {"$in": creator_ids}

    # origin : original ou forked
    if origin:
        if origin not in ("original", "forked"):
            raise BadRequestError(
                "Valeur invalide pour 'origin'. Valeurs possibles : original, forked"
            )
        if origin == "original":
            filters["forked_from_id"] = None
        else:
            filters["forked_from_id"] = {"$ne": None}

    # created_from / created_to
    if created_from:
        filters.setdefault("created_at", {})
        try:
            filters["created_at"]["$gte"] = datetime.fromisoformat(created_from)
        except ValueError:
            raise BadRequestError(
                "Format invalide pour 'created_from'. Attendu : ISO 8601"
            )

    if created_to:
        filters.setdefault("created_at", {})
        try:
            filters["created_at"]["$lte"] = datetime.fromisoformat(created_to)
        except ValueError:
            raise BadRequestError(
                "Format invalide pour 'created_to'. Attendu : ISO 8601"
            )

    # status (flows uniquement)
    if status and valid_statuses:
        values = [s.strip() for s in status.split(",") if s.strip()]
        invalid = [v for v in values if v not in valid_statuses]
        if invalid:
            raise BadRequestError(
                f"Statut(s) invalide(s) : {', '.join(invalid)}. "
                f"Valeurs possibles : {', '.join(sorted(valid_statuses))}"
            )
        filters["status"] = {"$in": values}

    return filters


def resolve_sort(
    sort_by: str | None,
    sort_dir: str | None,
    allowed_fields: set[str],
    default: str = "-created_at",
) -> str:
    """
    Résout le champ de tri Beanie à partir de sort_by/sort_dir.
    Retourne un string comme "+name" ou "-created_at".
    Lève BadRequestError (400) si les valeurs sont invalides.
    """
    if not sort_by:
        return default

    if sort_by not in allowed_fields:
        raise BadRequestError(
            f"Valeur invalide pour 'sort_by'. Valeurs possibles : {', '.join(sorted(allowed_fields))}"
        )

    if sort_dir and sort_dir not in ("asc", "desc"):
        raise BadRequestError(
            "Valeur invalide pour 'sort_dir'. Valeurs possibles : asc, desc"
        )

    direction = sort_dir or "asc"
    prefix = "+" if direction == "asc" else "-"
    return f"{prefix}{sort_by}"
