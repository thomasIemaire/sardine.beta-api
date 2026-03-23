"""
Schémas Pydantic pour les dossiers et la corbeille.
"""

from datetime import datetime

from pydantic import BaseModel


class FolderCreate(BaseModel):
    """Création d'un sous-dossier."""
    name: str
    parent_id: str  # ID du dossier parent


class FolderRename(BaseModel):
    """Renommage d'un dossier."""
    name: str


class FolderMove(BaseModel):
    """Déplacement d'un dossier."""
    target_parent_id: str  # ID du nouveau dossier parent


class FolderRead(BaseModel):
    """Réponse de lecture d'un dossier."""
    id: str
    name: str
    organization_id: str
    parent_id: str | None
    is_root: bool
    is_trash: bool
    created_at: datetime

    @classmethod
    def from_folder(cls, folder) -> "FolderRead":
        return cls(
            id=str(folder.id),
            name=folder.name,
            organization_id=str(folder.organization_id),
            parent_id=str(folder.parent_id) if folder.parent_id else None,
            is_root=folder.is_root,
            is_trash=folder.is_trash,
            created_at=folder.created_at,
        )


class TrashItemRead(BaseModel):
    """Élément dans la corbeille avec dates de suppression/expiration."""
    id: str
    name: str
    deleted_at: datetime
    expires_at: datetime  # deleted_at + 30 jours (rétention)

    @classmethod
    def from_folder(cls, folder) -> "TrashItemRead":
        from datetime import timedelta
        return cls(
            id=str(folder.id),
            name=folder.name,
            deleted_at=folder.deleted_at,
            # Date d'expiration = suppression + 30 jours de rétention
            expires_at=folder.deleted_at + timedelta(days=30),
        )


class BreadcrumbItem(BaseModel):
    """Élément du fil d'Ariane."""
    id: str
    name: str
