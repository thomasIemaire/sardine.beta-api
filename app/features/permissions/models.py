"""
Modèles de permissions sur les dossiers.

Architecture :
  - FolderTeamPermission : droits d'une équipe sur un dossier (lecture / écriture).
    Clé logique unique : (team_id, folder_id).
  - FolderMemberPermission : droits individuels d'un membre sur un dossier,
    dans le contexte d'une équipe. Clé logique unique : (user_id, team_id, folder_id).

Règles clés :
  - Deny-by-default : aucun droit = aucun accès.
  - Écriture implique systématiquement lecture.
  - Résolution multi-équipe : union (le plus permissif gagne).
  - Plafonnement : sous-équipe ≤ équipe parente, individuel ≤ équipe.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class FolderTeamPermission(Document):
    """Droits d'une équipe sur un dossier."""

    team_id: Indexed(PydanticObjectId)
    folder_id: Indexed(PydanticObjectId)

    can_read: bool = False
    can_write: bool = False  # Implique can_read

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "folder_team_permissions"


class FolderMemberPermission(Document):
    """
    Droits individuels d'un membre sur un dossier,
    dans le contexte d'une équipe spécifique.
    """

    user_id: Indexed(PydanticObjectId)
    team_id: Indexed(PydanticObjectId)
    folder_id: Indexed(PydanticObjectId)

    can_read: bool = False
    can_write: bool = False  # Implique can_read

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "folder_member_permissions"
