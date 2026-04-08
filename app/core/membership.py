"""
Vérification d'appartenance à une organisation.
Partagé entre les features agents, flows, etc.
"""

from beanie import PydanticObjectId

from app.core.enums import Status, TeamMemberRole
from app.core.exceptions import ForbiddenError, NotFoundError
from app.features.auth.models import User
from app.features.organizations.models import Organization
from app.features.teams.models import Team, TeamMember


async def check_org_membership(user: User, org_id: str) -> Organization:
    """
    Vérifie que l'utilisateur est membre de l'organisation
    (via l'équipe racine). Retourne l'organisation.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")

    root_team = await Team.find_one(
        Team.organization_id == org.id,
        Team.is_root == True,  # noqa: E712
    )
    if not root_team:
        raise NotFoundError("Équipe racine non trouvée")

    membership = await TeamMember.find_one(
        TeamMember.team_id == root_team.id,
        TeamMember.user_id == user.id,
        TeamMember.status == Status.ACTIVE,
    )
    if not membership:
        raise ForbiddenError("Vous n'êtes pas membre de cette organisation")

    return org


async def get_org_member_user_ids(org_id: str | PydanticObjectId) -> list[str]:
    """
    Retourne la liste de tous les user_id membres actifs d'une organisation.
    Inclut le owner_id de l'org + tous les membres actifs de l'équipe racine
    (peu importe leur rôle owner/member).
    """
    org_oid = org_id if isinstance(org_id, PydanticObjectId) else PydanticObjectId(org_id)
    org = await Organization.get(org_oid)
    if not org:
        return []

    user_ids: set[str] = {str(org.owner_id)}

    root_team = await Team.find_one(
        Team.organization_id == org.id,
        Team.is_root == True,  # noqa: E712
    )
    if root_team:
        members = await TeamMember.find(
            TeamMember.team_id == root_team.id,
            TeamMember.status == Status.ACTIVE,
        ).to_list()
        user_ids.update(str(m.user_id) for m in members)

    return list(user_ids)


async def get_org_owner_user_ids(org_id: str | PydanticObjectId) -> list[str]:
    """
    Retourne la liste des user_id qui sont propriétaires d'une organisation.
    Inclut le owner_id de l'org + tous les membres OWNER actifs de l'équipe racine.
    """
    org_oid = org_id if isinstance(org_id, PydanticObjectId) else PydanticObjectId(org_id)
    org = await Organization.get(org_oid)
    if not org:
        return []

    owner_ids: set[str] = {str(org.owner_id)}

    root_team = await Team.find_one(
        Team.organization_id == org.id,
        Team.is_root == True,  # noqa: E712
    )
    if root_team:
        owners = await TeamMember.find(
            TeamMember.team_id == root_team.id,
            TeamMember.role == TeamMemberRole.OWNER,
            TeamMember.status == Status.ACTIVE,
        ).to_list()
        owner_ids.update(str(m.user_id) for m in owners)

    return list(owner_ids)
