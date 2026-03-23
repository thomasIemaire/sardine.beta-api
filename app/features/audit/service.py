"""
Service de consultation du journal d'audit (US-FILE-12).
Lecture seule — aucune entree ne peut etre supprimee ou modifiee.
Accessible uniquement par le proprietaire de l'organisation.
"""

import re

from beanie import PydanticObjectId

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.pagination import paginate
from app.features.audit.models import AuditLog
from app.features.auth.models import User
from app.features.organizations.models import Organization


async def list_audit_logs(
    user: User,
    org_id: str,
    action: str | None = None,
    user_id_filter: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 30,
):
    """
    Liste paginee du journal d'audit d'une organisation.
    Filtres : action, utilisateur, recherche dans les details.
    Seul le proprietaire de l'organisation peut consulter.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvee")
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError(
            "Seul le proprietaire de l'organisation peut consulter le journal"
        )

    filters: dict = {"organization_id": org_id}

    if action:
        filters["action"] = action

    if user_id_filter:
        filters["user_id"] = user_id_filter

    if search:
        filters["details"] = {"$regex": re.escape(search), "$options": "i"}

    query = AuditLog.find(filters)
    return await paginate(query, page, page_size, sort_field="-created_at")


async def get_audit_actions(user: User, org_id: str) -> list[str]:
    """
    Liste les types d'actions distincts presents dans le journal
    de l'organisation (pour alimenter un filtre cote front).
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvee")
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError(
            "Seul le proprietaire de l'organisation peut consulter le journal"
        )

    logs = await AuditLog.find(
        AuditLog.organization_id == org_id,
    ).to_list()

    return sorted({log.action for log in logs})
