"""
Schemas Pydantic pour la gestion des datasets.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ─── Requêtes ──────────────────────────────────────────────────


class DatasetCreate(BaseModel):
    """Création d'un dataset."""

    name: str


class DatasetRename(BaseModel):
    """Renommage d'un dataset."""

    name: str


class PageUpdate(BaseModel):
    """Mise à jour d'une page (processed / document_type)."""

    processed: bool | None = None
    document_type: str | None = None


class ZoneInput(BaseModel):
    """Zone reçue depuis le front (sans _id, généré côté serveur)."""

    type: str
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    width: float = Field(gt=0, le=100)
    height: float = Field(gt=0, le=100)


class ZonesReplace(BaseModel):
    """Remplacement intégral des zones d'une page."""

    zones: list[ZoneInput]


# ─── Réponses ──────────────────────────────────────────────────


class ZoneRead(BaseModel):
    id: str
    type: str
    x: float
    y: float
    width: float
    height: float


class FileRead(BaseModel):
    id: str
    original_filename: str
    size: int
    page_count: int
    storage_id: str
    uploaded_at: datetime


class PageSummaryRead(BaseModel):
    """Page sans zones (pour le listing)."""

    id: str
    original_filename: str
    page_number: int
    processed: bool
    document_type: str | None
    zone_count: int


class PageDetailRead(BaseModel):
    """Page avec zones (pour le détail)."""

    id: str
    file_id: str
    original_filename: str
    page_number: int
    processed: bool
    document_type: str | None
    zones: list[ZoneRead]


class DatasetSummaryRead(BaseModel):
    """Dataset résumé pour le listing (sans pages)."""

    id: str
    name: str
    status: str
    page_count: int
    processed_count: int
    file_count: int
    created_at: datetime
    updated_at: datetime


class DatasetDetailRead(BaseModel):
    """Dataset complet avec fichiers, pages et classes disponibles."""

    id: str
    name: str
    status: str
    files: list[FileRead]
    pages: list[PageDetailRead]
    model_classes: list[str] = []
    custom_classes: list[str] = []
    created_at: datetime
    updated_at: datetime


class CustomClassAdd(BaseModel):
    """Ajout d'une classe personnalisée au dataset."""

    name: str


class ImportResult(BaseModel):
    """Résultat de l'import d'un fichier PDF."""

    original_filename: str
    pages_created: int
    dataset_status: str
