"""
Schémas Pydantic pour les permissions sur les dossiers.
"""

from datetime import datetime

from pydantic import BaseModel

# ─── Requêtes ────────────────────────────────────────────────────

class TeamPermissionSet(BaseModel):
    """Attribution / modification des droits d'une équipe sur un dossier."""

    team_id: str
    folder_id: str
    can_read: bool = False
    can_write: bool = False


class MemberPermissionSet(BaseModel):
    """
    Attribution / modification des droits individuels d'un membre.
    Si team_id est omis, l'équipe racine de l'organisation est utilisée.
    """

    user_id: str
    team_id: str | None = None
    folder_id: str
    can_read: bool = False
    can_write: bool = False


# ─── Réponses ────────────────────────────────────────────────────

class TeamPermissionRead(BaseModel):
    """Lecture des droits d'une équipe sur un dossier."""

    id: str
    team_id: str
    folder_id: str
    can_read: bool
    can_write: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_perm(cls, perm) -> "TeamPermissionRead":
        return cls(
            id=str(perm.id),
            team_id=str(perm.team_id),
            folder_id=str(perm.folder_id),
            can_read=perm.can_read,
            can_write=perm.can_write,
            created_at=perm.created_at,
            updated_at=perm.updated_at,
        )


class MemberPermissionRead(BaseModel):
    """Lecture des droits individuels d'un membre."""

    id: str
    user_id: str
    team_id: str
    folder_id: str
    can_read: bool
    can_write: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_perm(cls, perm) -> "MemberPermissionRead":
        return cls(
            id=str(perm.id),
            user_id=str(perm.user_id),
            team_id=str(perm.team_id),
            folder_id=str(perm.folder_id),
            can_read=perm.can_read,
            can_write=perm.can_write,
            created_at=perm.created_at,
            updated_at=perm.updated_at,
        )


class EffectiveRight(BaseModel):
    """Droit effectif d'un utilisateur sur un dossier."""

    folder_id: str
    folder_name: str
    can_read: bool
    can_write: bool
    sources: list[dict]


class FolderAccessUser(BaseModel):
    """Utilisateur ayant accès à un dossier, avec son droit effectif."""

    user_id: str
    first_name: str
    last_name: str
    can_read: bool
    can_write: bool
    sources: list[dict]


class MemberPermissionDetail(BaseModel):
    """Droits d'un membre sur un dossier : droit équipe + droit individuel."""

    folder_id: str
    folder_name: str
    team_can_read: bool
    team_can_write: bool
    individual_can_read: bool | None = None
    individual_can_write: bool | None = None


class FolderTeamPermissionEntry(BaseModel):
    """Permission d'une équipe sur un dossier (vue admin)."""
    team_id: str
    team_name: str
    is_root: bool
    can_read: bool
    can_write: bool


class FolderMemberPermissionEntry(BaseModel):
    """Permission individuelle d'un membre sur un dossier (vue admin)."""
    user_id: str
    first_name: str
    last_name: str
    email: str
    team_id: str
    team_name: str
    can_read: bool
    can_write: bool


class FolderPermissionsBreakdown(BaseModel):
    """Décomposition des permissions sur un dossier : équipes + individuels."""
    teams: list[FolderTeamPermissionEntry]
    members: list[FolderMemberPermissionEntry]


class CascadeImpact(BaseModel):
    """Impact d'une réduction en cascade avant confirmation."""

    team_permissions_impacted: int
    member_permissions_impacted: int
