"""
Service datasets — création, import PDF, gestion des pages/zones, export.

Stockage physique : storage/datasets/{uuid}.pdf
Métadonnées en MongoDB (Dataset avec fichiers/pages embarqués).
"""

import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from beanie import PydanticObjectId
from pypdf import PdfReader, PdfWriter

from app.core.audit import log_action
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.features.auth.models import User
from app.features.datasets.models import (
    Dataset,
    DatasetFile,
    DatasetPage,
    DatasetZone,
)
from app.features.organizations.models import Organization

STORAGE_DIR = Path("storage/datasets")

VALID_ZONE_TYPES = {"text", "image", "table"}
VALID_DOCUMENT_TYPES = {
    "invoice",
    "invoice_next",
    "payslip",
    "contract",
    "quote",
    "purchase_order",
    "credit_note",
    "bank_statement",
    "certificate",
    "terms_of_service",
    "terms_of_sale",
}


# ─── Helpers ───────────────────────────────────────────────────


def _storage_path(storage_id: str) -> Path:
    """Chemin complet vers un fichier PDF du dataset."""
    return STORAGE_DIR / f"{storage_id}.pdf"


def _recalc_status(dataset: Dataset) -> str:
    """Recalcule le statut du dataset selon l'état des pages."""
    if not dataset.pages:
        return "draft"
    if all(p.processed for p in dataset.pages):
        return "ready"
    return "in_progress"


async def _get_dataset(dataset_id: str, org_id: str) -> Dataset:
    """Récupère un dataset et vérifie qu'il appartient à l'organisation."""
    dataset = await Dataset.get(PydanticObjectId(dataset_id))
    if not dataset:
        raise NotFoundError("Dataset not found")
    if str(dataset.organization_id) != org_id:
        raise NotFoundError("Dataset not found")
    return dataset


async def _assert_org_member(user: User, org_id: str) -> None:
    """Vérifie que l'utilisateur a accès à l'organisation."""
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")


# ─── CRUD Datasets ─────────────────────────────────────────────


async def create_dataset(user: User, org_id: str, name: str) -> Dataset:
    """Crée un dataset vide."""
    await _assert_org_member(user, org_id)

    if not name or not name.strip():
        raise BadRequestError("name is required")

    dataset = Dataset(
        name=name.strip(),
        organization_id=PydanticObjectId(org_id),
        created_by=user.id,
    )
    await dataset.insert()

    await log_action(
        user.id, "DATASET_CREATE",
        f"Dataset '{dataset.name}' créé",
    )
    return dataset


async def list_datasets(user: User, org_id: str) -> list[Dataset]:
    """Liste tous les datasets d'une organisation."""
    await _assert_org_member(user, org_id)

    return await Dataset.find(
        Dataset.organization_id == PydanticObjectId(org_id),
    ).sort("-created_at").to_list()


async def get_dataset_detail(user: User, org_id: str, dataset_id: str) -> Dataset:
    """Retourne le détail complet d'un dataset."""
    await _assert_org_member(user, org_id)
    return await _get_dataset(dataset_id, org_id)


async def rename_dataset(
    user: User, org_id: str, dataset_id: str, name: str,
) -> Dataset:
    """Renomme un dataset."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    if not name or not name.strip():
        raise BadRequestError("name is required")

    await dataset.set({
        "name": name.strip(),
        "updated_at": datetime.now(UTC),
    })

    await log_action(
        user.id, "DATASET_RENAME",
        f"Dataset renommé en '{name.strip()}'",
    )
    return dataset


async def delete_dataset(user: User, org_id: str, dataset_id: str) -> None:
    """Supprime définitivement un dataset et tous ses fichiers sur disque."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    # Supprimer les fichiers physiques (files + pages)
    for f in dataset.files:
        path = _storage_path(f.storage_id)
        if path.exists():
            path.unlink()

    for p in dataset.pages:
        path = _storage_path(p.storage_id)
        if path.exists():
            path.unlink()

    await dataset.delete()

    await log_action(
        user.id, "DATASET_DELETE",
        f"Dataset '{dataset.name}' supprimé",
    )


# ─── Import de fichiers PDF ───────────────────────────────────


async def import_pdf(
    user: User, org_id: str, dataset_id: str, content: bytes, filename: str,
) -> tuple[Dataset, int]:
    """
    Importe un fichier PDF dans un dataset.
    Stocke le PDF complet, extrait chaque page en PDF unitaire.
    Retourne (dataset mis à jour, nombre de pages créées).
    """
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    # Valider que c'est un PDF
    if not filename.lower().endswith(".pdf"):
        raise BadRequestError("Only PDF files are accepted")

    # Lire le PDF
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        raise ValidationError("Could not extract pages from this PDF")

    page_count = len(reader.pages)
    if page_count == 0:
        raise ValidationError("Could not extract pages from this PDF")

    # Stocker le PDF complet
    file_storage_id = str(uuid.uuid4())
    file_path = _storage_path(file_storage_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)

    # Créer l'entrée fichier
    file_id = PydanticObjectId()
    dataset_file = DatasetFile(
        id=file_id,
        original_filename=filename,
        size=len(content),
        page_count=page_count,
        storage_id=file_storage_id,
    )

    # Extraire chaque page en PDF unitaire
    new_pages: list[DatasetPage] = []
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        page_buffer = io.BytesIO()
        writer.write(page_buffer)
        page_bytes = page_buffer.getvalue()

        page_storage_id = str(uuid.uuid4())
        page_path = _storage_path(page_storage_id)
        page_path.write_bytes(page_bytes)

        dataset_page = DatasetPage(
            file_id=file_id,
            original_filename=filename,
            page_number=i + 1,
            storage_id=page_storage_id,
        )
        new_pages.append(dataset_page)

    # Mettre à jour le dataset
    dataset.files.append(dataset_file)
    dataset.pages.extend(new_pages)
    dataset.status = _recalc_status(dataset)
    dataset.updated_at = datetime.now(UTC)
    await dataset.save()

    await log_action(
        user.id, "DATASET_IMPORT",
        f"Fichier '{filename}' importé ({page_count} pages) dans dataset '{dataset.name}'",
    )
    return dataset, page_count


# ─── Binaires ──────────────────────────────────────────────────


async def get_file_binary(
    user: User, org_id: str, dataset_id: str, file_id: str,
) -> tuple[Path, str]:
    """Retourne le chemin physique du PDF complet d'un fichier importé."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    file_doc = next(
        (f for f in dataset.files if str(f.id) == file_id), None,
    )
    if not file_doc:
        raise NotFoundError("File not found")

    path = _storage_path(file_doc.storage_id)
    if not path.exists():
        raise NotFoundError("File not found")

    return path, file_doc.original_filename


async def get_page_binary(
    user: User, org_id: str, dataset_id: str, page_id: str,
) -> tuple[Path, str]:
    """Retourne le chemin physique du PDF d'une page."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    page = next(
        (p for p in dataset.pages if str(p.id) == page_id), None,
    )
    if not page:
        raise NotFoundError("Page not found")

    path = _storage_path(page.storage_id)
    if not path.exists():
        raise NotFoundError("Page not found")

    return path, f"{page.original_filename}_page{page.page_number}.pdf"


# ─── Pages ─────────────────────────────────────────────────────


async def list_pages(
    user: User, org_id: str, dataset_id: str,
    processed: bool | None = None,
    filename: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Liste les pages d'un dataset avec filtrage et pagination."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    pages = dataset.pages

    # Filtrage
    if processed is not None:
        pages = [p for p in pages if p.processed == processed]
    if filename:
        pages = [p for p in pages if filename.lower() in p.original_filename.lower()]

    total = len(pages)

    # Pagination
    start = (page - 1) * limit
    end = start + limit
    paginated = pages[start:end]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "data": paginated,
    }


async def get_page_detail(
    user: User, org_id: str, dataset_id: str, page_id: str,
) -> DatasetPage:
    """Retourne le détail d'une page avec ses zones."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    page = next(
        (p for p in dataset.pages if str(p.id) == page_id), None,
    )
    if not page:
        raise NotFoundError("Page not found")

    return page


async def update_page(
    user: User, org_id: str, dataset_id: str, page_id: str,
    processed: bool | None = None, document_type: str | None = None,
) -> tuple[DatasetPage, Dataset]:
    """Met à jour processed et/ou document_type d'une page."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    page = next(
        (p for p in dataset.pages if str(p.id) == page_id), None,
    )
    if not page:
        raise NotFoundError("Page not found")

    if document_type is not None and document_type not in VALID_DOCUMENT_TYPES:
        raise BadRequestError(
            f"Invalid document_type, allowed: {', '.join(sorted(VALID_DOCUMENT_TYPES))}"
        )

    if processed is not None:
        page.processed = processed
    if document_type is not None:
        page.document_type = document_type

    # Recalculer le statut du dataset
    dataset.status = _recalc_status(dataset)
    dataset.updated_at = datetime.now(UTC)
    await dataset.save()

    await log_action(
        user.id, "DATASET_PAGE_UPDATE",
        f"Page {page.page_number} de '{page.original_filename}' mise à jour",
    )
    return page, dataset


# ─── Zones ─────────────────────────────────────────────────────


async def replace_zones(
    user: User, org_id: str, dataset_id: str, page_id: str,
    zones: list[dict],
) -> list[DatasetZone]:
    """Remplace intégralement les zones d'une page."""
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    page = next(
        (p for p in dataset.pages if str(p.id) == page_id), None,
    )
    if not page:
        raise NotFoundError("Page not found")

    # Valider les zones
    new_zones: list[DatasetZone] = []
    for z in zones:
        if z.get("type") not in VALID_ZONE_TYPES:
            raise BadRequestError(
                f"Invalid zone type, allowed: {', '.join(sorted(VALID_ZONE_TYPES))}"
            )

        x, y = z["x"], z["y"]
        w, h = z["width"], z["height"]
        if x + w > 100 or y + h > 100:
            raise BadRequestError("Zone coordinates out of bounds (x+width <= 100, y+height <= 100)")

        new_zones.append(DatasetZone(
            type=z["type"],
            x=x,
            y=y,
            width=w,
            height=h,
        ))

    page.zones = new_zones
    dataset.updated_at = datetime.now(UTC)
    await dataset.save()

    await log_action(
        user.id, "DATASET_ZONES_UPDATE",
        f"Zones de la page {page.page_number} mises à jour ({len(new_zones)} zones)",
    )
    return new_zones


# ─── Export ────────────────────────────────────────────────────


async def export_dataset(
    user: User, org_id: str, dataset_id: str, fmt: str = "jsonl",
) -> tuple[str, str]:
    """
    Exporte le dataset en JSONL ou JSON.
    Retourne (contenu, nom_fichier).
    """
    await _assert_org_member(user, org_id)
    dataset = await _get_dataset(dataset_id, org_id)

    if dataset.status != "ready":
        unprocessed = sum(1 for p in dataset.pages if not p.processed)
        raise ConflictError(
            f"Dataset is not ready (status: {dataset.status}, "
            f"unprocessed_count: {unprocessed})"
        )

    lines = []
    for page in dataset.pages:
        entry = {
            "page_id": str(page.id),
            "filename": page.original_filename,
            "page": page.page_number,
            "document_type": page.document_type,
            "zones": [
                {
                    "type": z.type,
                    "x": z.x,
                    "y": z.y,
                    "width": z.width,
                    "height": z.height,
                }
                for z in page.zones
            ],
        }
        lines.append(entry)

    if fmt == "json":
        content = json.dumps(lines, ensure_ascii=False, indent=2)
        filename = f"dataset-{dataset_id}.json"
    else:
        content = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines)
        filename = f"dataset-{dataset_id}.jsonl"

    await log_action(
        user.id, "DATASET_EXPORT",
        f"Dataset '{dataset.name}' exporté en {fmt}",
    )
    return content, filename
