"""
Modèle Organisation.
Chaque organisation possède :
  - un dossier racine (créé automatiquement)
  - un dossier corbeille (créé automatiquement)
  - une équipe racine (créée automatiquement)
Le champ is_private distingue l'organisation personnelle
(créée auto à l'inscription) des organisations collaboratives.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.core.enums import Status


class Organization(Document):
    name: Indexed(str)
    owner_id: PydanticObjectId  # Utilisateur créateur / propriétaire

    # True = organisation personnelle "Prénom Nom"
    is_private: bool = False

    status: int = Field(default=Status.ACTIVE)

    # Restrictions de fichiers (US-FILE-03) — null = pas de restriction
    max_file_size_mb: int | None = None  # Taille max par fichier en Mo
    allowed_extensions: list[str] | None = None  # Ex: [".pdf", ".docx", ".jpg"]

    # Champs optionnels
    external_reference: str | None = None
    distributor_org_id: PydanticObjectId | None = None  # Organisation distributrice
    parent_org_id: PydanticObjectId | None = None        # Organisation mère

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "organizations"
