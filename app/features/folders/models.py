"""
Modèle Folder — gestion de l'arborescence documentaire.

Chaque organisation a exactement un dossier racine (is_root=True)
et un dossier corbeille (is_trash=True). Ces dossiers système
ne peuvent être ni supprimés ni renommés.

Suppression douce : un élément supprimé est déplacé dans la corbeille
(deleted_at est positionné). Après 30 jours de rétention, le système
purge définitivement l'élément.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class Folder(Document):
    name: str
    organization_id: Indexed(PydanticObjectId)

    # parent_id=None signifie dossier racine de l'organisation
    parent_id: PydanticObjectId | None = None

    # Dossiers système non modifiables
    is_root: bool = False
    is_trash: bool = False

    # Champs de suppression douce
    deleted_at: datetime | None = None
    # Sauvegarde du parent d'origine pour la restauration
    original_parent_id: PydanticObjectId | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "folders"
