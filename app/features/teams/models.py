"""
Modèles pour la gestion des équipes.

Architecture :
  - Team : équipe rattachée à une organisation
  - TeamMember : relation N-N entre User et Team, avec rôle (Owner/Member)
  - TeamHierarchy : relation N-N parent/enfant entre équipes
    Permet à une équipe d'avoir plusieurs parents et plusieurs enfants.

L'héritage des droits propriétaire est calculé dynamiquement
à la lecture, jamais dupliqué en base.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.core.enums import Status, TeamMemberRole


class Team(Document):
    name: str
    organization_id: Indexed(PydanticObjectId)

    # L'équipe racine est créée auto avec l'organisation.
    # Elle ne peut être ni supprimée ni renommée.
    is_root: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "teams"


class TeamMember(Document):
    """
    Membership d'un utilisateur dans une équipe.
    Clé logique unique : (team_id, user_id).
    """
    team_id: Indexed(PydanticObjectId)
    user_id: Indexed(PydanticObjectId)

    # 1 = Propriétaire, 2 = Membre (défaut Membre)
    role: int = Field(default=TeamMemberRole.MEMBER)

    # 1 = Actif, 0 = Inactif
    status: int = Field(default=Status.ACTIVE)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "team_members"


class TeamHierarchy(Document):
    """
    Relation parent-enfant entre équipes.
    Relation N-N : une équipe peut avoir plusieurs parents et plusieurs enfants.
    """
    parent_team_id: Indexed(PydanticObjectId)
    child_team_id: Indexed(PydanticObjectId)

    class Settings:
        name = "team_hierarchy"
