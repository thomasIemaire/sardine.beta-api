"""
Helpers de resolution batch des utilisateurs.
Permet d'enrichir les reponses avec les noms d'utilisateurs sans
faire un appel par item.
"""

from beanie import PydanticObjectId

from app.features.auth.models import User


def _format_name(user: User) -> str:
    """Formate 'Prenom Nom' en gerant les chaines vides."""
    parts = [(user.first_name or "").strip(), (user.last_name or "").strip()]
    name = " ".join(p for p in parts if p)
    return name or user.email


async def get_user_names_map(user_ids) -> dict[str, str]:
    """
    Charge en une seule requete les utilisateurs et retourne
    un dict {str(user_id): 'Prenom Nom'}.
    Accepte des PydanticObjectId, str, ou un mix.
    """
    # Normaliser en PydanticObjectId, en filtrant les None
    oids: list[PydanticObjectId] = []
    seen: set[str] = set()
    for uid in user_ids:
        if uid is None:
            continue
        s = str(uid)
        if s in seen:
            continue
        seen.add(s)
        try:
            oids.append(PydanticObjectId(s))
        except Exception:
            continue

    if not oids:
        return {}

    users = await User.find({"_id": {"$in": oids}}).to_list()
    return {str(u.id): _format_name(u) for u in users}
