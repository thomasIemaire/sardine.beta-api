"""
Vérification d'appartenance à une organisation.
Partagé entre les features agents, flows, etc.
"""

from beanie import PydanticObjectId

from app.core.enums import Status
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
