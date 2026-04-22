"""
Modèle pour la gestion des datasets IA.

Architecture :
  - Dataset : document principal avec fichiers et pages embarqués.
    Les fichiers PDF importés sont stockés dans files[].
    Les pages extraites sont stockées dans pages[] avec leurs zones.
  - Le stockage physique est dans storage/datasets/{uuid}.pdf.
"""

from datetime import UTC, datetime

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field


# ─── Sous-documents embarqués ──────────────────────────────────


class DatasetZone(BaseModel):
    """Zone annotée sur une page PDF (coordonnées en % 0-100)."""

    id: PydanticObjectId = Field(default_factory=PydanticObjectId)
    type: str  # "text" | "image" | "table"
    x: float
    y: float
    width: float
    height: float


class DatasetPage(BaseModel):
    """Page individuelle extraite d'un fichier PDF importé."""

    id: PydanticObjectId = Field(default_factory=PydanticObjectId)
    file_id: PydanticObjectId
    original_filename: str
    page_number: int
    storage_id: str  # UUID → storage/datasets/{uuid}.pdf
    processed: bool = False
    document_type: str | None = None
    zones: list[DatasetZone] = []


class DatasetFile(BaseModel):
    """Fichier PDF importé dans le dataset."""

    id: PydanticObjectId = Field(default_factory=PydanticObjectId)
    original_filename: str
    size: int  # octets
    page_count: int
    storage_id: str  # UUID → storage/datasets/{uuid}.pdf
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─── Document principal ────────────────────────────────────────


class Dataset(Document):
    """Dataset IA avec fichiers et pages embarqués."""

    name: str
    status: str = "draft"  # "draft" | "in_progress" | "ready"

    organization_id: PydanticObjectId

    files: list[DatasetFile] = []
    pages: list[DatasetPage] = []
    custom_classes: list[str] = []

    created_by: PydanticObjectId

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "datasets"
