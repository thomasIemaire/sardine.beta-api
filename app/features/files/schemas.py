"""
Schemas Pydantic pour la gestion des fichiers.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel

RETENTION_DAYS = 30


# ─── Requetes ────────────────────────────────────────────────────

class FileRename(BaseModel):
    """Renommage d'un fichier."""

    name: str


class FileMove(BaseModel):
    """Deplacement d'un fichier vers un autre dossier."""

    target_folder_id: str


# ─── Reponses ────────────────────────────────────────────────────

class FileRead(BaseModel):
    """Lecture d'un fichier."""

    id: str
    name: str
    folder_id: str
    organization_id: str
    current_version: int
    mime_type: str
    size: int
    uploaded_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_file(cls, f) -> "FileRead":
        return cls(
            id=str(f.id),
            name=f.name,
            folder_id=str(f.folder_id),
            organization_id=str(f.organization_id),
            current_version=f.current_version,
            mime_type=f.mime_type,
            size=f.size,
            uploaded_by=str(f.uploaded_by),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )


class FileVersionRead(BaseModel):
    """Lecture d'une version de fichier."""

    id: str
    file_id: str
    version_number: int
    original_name: str
    mime_type: str
    size: int
    uploaded_by: str
    created_at: datetime

    @classmethod
    def from_version(cls, v) -> "FileVersionRead":
        return cls(
            id=str(v.id),
            file_id=str(v.file_id),
            version_number=v.version_number,
            original_name=v.original_name,
            mime_type=v.mime_type,
            size=v.size,
            uploaded_by=str(v.uploaded_by),
            created_at=v.created_at,
        )


class TrashFileRead(BaseModel):
    """Fichier dans la corbeille."""

    id: str
    name: str
    mime_type: str
    size: int
    deleted_at: datetime
    expires_at: datetime

    @classmethod
    def from_file(cls, f) -> "TrashFileRead":
        return cls(
            id=str(f.id),
            name=f.name,
            mime_type=f.mime_type,
            size=f.size,
            deleted_at=f.deleted_at,
            expires_at=f.deleted_at + timedelta(days=RETENTION_DAYS),
        )


class UploadResult(BaseModel):
    """Resultat d'un upload (simple ou multiple)."""

    success: list[FileRead] = []
    errors: list[dict] = []


class BulkDeleteRequest(BaseModel):
    """Suppression en masse de fichiers et/ou dossiers."""

    file_ids: list[str] = []
    folder_ids: list[str] = []


class BulkDeleteResult(BaseModel):
    """Resultat d'une suppression en masse."""

    files_deleted: int
    folders_deleted: int
    skipped: int
    details: list[dict] = []
