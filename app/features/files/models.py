"""
Modèles pour la gestion des fichiers.

Architecture :
  - File : métadonnées du fichier (nom, dossier, organisation).
    Supporte le soft-delete vers la corbeille (comme les dossiers).
  - FileVersion : historique des versions d'un fichier.
    Chaque upload crée une nouvelle version (v1, v2, v3…).
    Les anciennes versions sont conservées et téléchargeables.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class File(Document):
    """Métadonnées d'un fichier dans un dossier d'une organisation."""

    name: str  # Nom complet avec extension (ex: "document.pdf")
    folder_id: Indexed(PydanticObjectId)
    organization_id: Indexed(PydanticObjectId)

    # Version courante
    current_version: int = 1

    # Métadonnées du fichier courant
    mime_type: str = ""
    size: int = 0  # Taille en octets de la version courante

    # Auteur du dépôt initial
    uploaded_by: PydanticObjectId

    # Soft delete (même pattern que Folder)
    deleted_at: datetime | None = None
    original_folder_id: PydanticObjectId | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "files"


class FileVersion(Document):
    """
    Version d'un fichier. Chaque dépôt/remplacement crée une entrée.
    Le fichier physique est stocké sur disque dans storage/files/.
    """

    file_id: Indexed(PydanticObjectId)
    version_number: int  # 1, 2, 3…

    # Chemin relatif vers le fichier physique (storage/files/...)
    storage_path: str

    # Métadonnées de cette version
    original_name: str  # Nom du fichier uploadé pour cette version
    mime_type: str = ""
    size: int = 0  # Taille en octets

    uploaded_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "file_versions"
