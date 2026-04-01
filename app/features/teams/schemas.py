"""
Schémas Pydantic pour les équipes, membres et sous-équipes.
"""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import STATUS_LABELS, TEAM_ROLE_LABELS


class TeamCreate(BaseModel):
    """Création d'une équipe dans une organisation."""
    name: str


class TeamUpdate(BaseModel):
    """Renommage d'une équipe."""
    name: str


class TeamRead(BaseModel):
    """Réponse de lecture d'une équipe."""
    id: str
    name: str
    organization_id: str
    is_root: bool
    created_at: datetime

    @classmethod
    def from_team(cls, team) -> "TeamRead":
        return cls(
            id=str(team.id),
            name=team.name,
            organization_id=str(team.organization_id),
            is_root=team.is_root,
            created_at=team.created_at,
        )


class MemberAdd(BaseModel):
    """Ajout d'un membre à une équipe."""
    user_id: str


class MemberRoleUpdate(BaseModel):
    """Modification du rôle d'un membre (1=Owner, 2=Member)."""
    role: int


class MemberStatusUpdate(BaseModel):
    """Activation/désactivation d'un membre."""
    status: int  # 0 = Inactif, 1 = Actif


class MemberRead(BaseModel):
    """
    Lecture d'un membre d'équipe.
    Le champ 'inherited' indique si les droits propriétaire
    sont hérités d'une équipe parente.
    """
    user_id: str
    email: str
    first_name: str
    last_name: str
    role: int
    role_label: str
    status: int
    status_label: str
    inherited: bool = False  # True si propriétaire hérité

    @classmethod
    def from_member(cls, member, user, inherited: bool = False) -> "MemberRead":
        return cls(
            user_id=str(member.user_id),
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=member.role,
            role_label=TEAM_ROLE_LABELS.get(member.role, "Inconnu"),
            status=member.status,
            status_label=STATUS_LABELS.get(member.status, "Inconnu"),
            inherited=inherited,
        )


class SubTeamCreate(BaseModel):
    """Rattachement d'une sous-équipe à un ou plusieurs parents."""
    name: str
    parent_team_ids: list[str]  # Relation N-N


class TeamTreeNode(BaseModel):
    """Noeud dans l'arborescence des équipes."""
    id: str
    name: str
    is_root: bool
    is_member: bool  # L'utilisateur courant est-il membre ?
    children: list["TeamTreeNode"] = []
