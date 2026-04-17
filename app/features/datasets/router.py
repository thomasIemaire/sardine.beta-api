"""
Routes gestion des datasets.
Toutes les routes nécessitent un rôle administrateur (role = 0).
"""

from fastapi import APIRouter, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.features.auth.dependencies import CurrentAdmin
from app.features.datasets.schemas import (
    DatasetCreate,
    DatasetDetailRead,
    DatasetRename,
    DatasetSummaryRead,
    FileRead,
    ImportResult,
    PageDetailRead,
    PageSummaryRead,
    PageUpdate,
    ZoneRead,
    ZonesReplace,
)
from app.features.datasets.service import (
    create_dataset,
    delete_dataset,
    export_dataset,
    get_dataset_detail,
    get_file_binary,
    get_page_binary,
    get_page_detail,
    import_pdf,
    list_datasets,
    list_pages,
    rename_dataset,
    replace_zones,
    update_page,
)

router = APIRouter(
    prefix="/organizations/{org_id}/datasets",
    tags=["Datasets"],
)


# ─── CRUD Datasets ─────────────────────────────────────────────


@router.post("/", status_code=201)
async def create(
    org_id: str, payload: DatasetCreate, current_user: CurrentAdmin,
):
    """Créer un nouveau dataset vide."""
    dataset = await create_dataset(current_user, org_id, payload.name)
    return DatasetDetailRead(
        id=str(dataset.id),
        name=dataset.name,
        status=dataset.status,
        files=[],
        pages=[],
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


@router.get("/")
async def list_all(org_id: str, current_user: CurrentAdmin):
    """Lister tous les datasets avec compteurs de progression."""
    datasets = await list_datasets(current_user, org_id)
    return [
        DatasetSummaryRead(
            id=str(d.id),
            name=d.name,
            status=d.status,
            page_count=len(d.pages),
            processed_count=sum(1 for p in d.pages if p.processed),
            file_count=len(d.files),
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in datasets
    ]


@router.get("/{dataset_id}")
async def get_detail(org_id: str, dataset_id: str, current_user: CurrentAdmin):
    """Détail complet d'un dataset avec fichiers et pages."""
    d = await get_dataset_detail(current_user, org_id, dataset_id)
    return DatasetDetailRead(
        id=str(d.id),
        name=d.name,
        status=d.status,
        files=[
            FileRead(
                id=str(f.id),
                original_filename=f.original_filename,
                size=f.size,
                page_count=f.page_count,
                storage_id=f.storage_id,
                uploaded_at=f.uploaded_at,
            )
            for f in d.files
        ],
        pages=[
            PageDetailRead(
                id=str(p.id),
                file_id=str(p.file_id),
                original_filename=p.original_filename,
                page_number=p.page_number,
                processed=p.processed,
                document_type=p.document_type,
                zones=[
                    ZoneRead(
                        id=str(z.id),
                        type=z.type,
                        x=z.x, y=z.y,
                        width=z.width, height=z.height,
                    )
                    for z in p.zones
                ],
            )
            for p in d.pages
        ],
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.patch("/{dataset_id}")
async def rename(
    org_id: str, dataset_id: str,
    payload: DatasetRename, current_user: CurrentAdmin,
):
    """Renommer un dataset."""
    d = await rename_dataset(current_user, org_id, dataset_id, payload.name)
    return DatasetDetailRead(
        id=str(d.id),
        name=d.name,
        status=d.status,
        files=[
            FileRead(
                id=str(f.id),
                original_filename=f.original_filename,
                size=f.size,
                page_count=f.page_count,
                storage_id=f.storage_id,
                uploaded_at=f.uploaded_at,
            )
            for f in d.files
        ],
        pages=[
            PageDetailRead(
                id=str(p.id),
                file_id=str(p.file_id),
                original_filename=p.original_filename,
                page_number=p.page_number,
                processed=p.processed,
                document_type=p.document_type,
                zones=[
                    ZoneRead(
                        id=str(z.id),
                        type=z.type,
                        x=z.x, y=z.y,
                        width=z.width, height=z.height,
                    )
                    for z in p.zones
                ],
            )
            for p in d.pages
        ],
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.delete("/{dataset_id}", status_code=204)
async def delete(org_id: str, dataset_id: str, current_user: CurrentAdmin):
    """Supprimer définitivement un dataset."""
    await delete_dataset(current_user, org_id, dataset_id)


# ─── Import de fichiers ───────────────────────────────────────


@router.post("/{dataset_id}/import", status_code=201)
async def import_file(
    org_id: str, dataset_id: str,
    file: UploadFile, current_user: CurrentAdmin,
):
    """Importer un fichier PDF dans le dataset."""
    content = await file.read()
    filename = file.filename or "document.pdf"
    dataset, pages_created = await import_pdf(
        current_user, org_id, dataset_id, content, filename,
    )
    return ImportResult(
        original_filename=filename,
        pages_created=pages_created,
        dataset_status=dataset.status,
    )


# ─── Binaires ──────────────────────────────────────────────────


@router.get("/{dataset_id}/files/{file_id}/binary")
async def file_binary(
    org_id: str, dataset_id: str, file_id: str,
    current_user: CurrentAdmin,
):
    """Télécharger le PDF complet d'un fichier importé."""
    path, filename = await get_file_binary(
        current_user, org_id, dataset_id, file_id,
    )
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/pdf",
    )


@router.get("/{dataset_id}/pages/{page_id}/binary")
async def page_binary(
    org_id: str, dataset_id: str, page_id: str,
    current_user: CurrentAdmin,
):
    """Télécharger le PDF d'une page seule."""
    path, filename = await get_page_binary(
        current_user, org_id, dataset_id, page_id,
    )
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/pdf",
    )


# ─── Pages ─────────────────────────────────────────────────────


@router.get("/{dataset_id}/pages")
async def pages_list(
    org_id: str, dataset_id: str, current_user: CurrentAdmin,
    processed: bool | None = Query(None),
    filename: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """Lister les pages d'un dataset avec filtrage et pagination."""
    result = await list_pages(
        current_user, org_id, dataset_id,
        processed=processed, filename=filename,
        page=page, limit=limit,
    )
    return {
        "total": result["total"],
        "page": result["page"],
        "limit": result["limit"],
        "data": [
            PageSummaryRead(
                id=str(p.id),
                original_filename=p.original_filename,
                page_number=p.page_number,
                processed=p.processed,
                document_type=p.document_type,
                zone_count=len(p.zones),
            )
            for p in result["data"]
        ],
    }


@router.get("/{dataset_id}/pages/{page_id}")
async def page_detail(
    org_id: str, dataset_id: str, page_id: str,
    current_user: CurrentAdmin,
):
    """Détail d'une page avec ses zones."""
    p = await get_page_detail(current_user, org_id, dataset_id, page_id)
    return PageDetailRead(
        id=str(p.id),
        file_id=str(p.file_id),
        original_filename=p.original_filename,
        page_number=p.page_number,
        processed=p.processed,
        document_type=p.document_type,
        zones=[
            ZoneRead(
                id=str(z.id),
                type=z.type,
                x=z.x, y=z.y,
                width=z.width, height=z.height,
            )
            for z in p.zones
        ],
    )


@router.patch("/{dataset_id}/pages/{page_id}")
async def page_update(
    org_id: str, dataset_id: str, page_id: str,
    payload: PageUpdate, current_user: CurrentAdmin,
):
    """Mettre à jour processed / document_type d'une page."""
    p, _ = await update_page(
        current_user, org_id, dataset_id, page_id,
        processed=payload.processed,
        document_type=payload.document_type,
    )
    return PageDetailRead(
        id=str(p.id),
        file_id=str(p.file_id),
        original_filename=p.original_filename,
        page_number=p.page_number,
        processed=p.processed,
        document_type=p.document_type,
        zones=[
            ZoneRead(
                id=str(z.id),
                type=z.type,
                x=z.x, y=z.y,
                width=z.width, height=z.height,
            )
            for z in p.zones
        ],
    )


# ─── Zones ─────────────────────────────────────────────────────


@router.put("/{dataset_id}/pages/{page_id}/zones")
async def zones_replace(
    org_id: str, dataset_id: str, page_id: str,
    payload: ZonesReplace, current_user: CurrentAdmin,
):
    """Remplacer intégralement les zones d'une page."""
    zones_data = [z.model_dump() for z in payload.zones]
    new_zones = await replace_zones(
        current_user, org_id, dataset_id, page_id, zones_data,
    )
    return {
        "zones": [
            ZoneRead(
                id=str(z.id),
                type=z.type,
                x=z.x, y=z.y,
                width=z.width, height=z.height,
            )
            for z in new_zones
        ],
    }


# ─── Export ────────────────────────────────────────────────────


@router.get("/{dataset_id}/export")
async def export(
    org_id: str, dataset_id: str, current_user: CurrentAdmin,
    format: str = Query("jsonl", pattern="^(jsonl|json)$"),
):
    """Exporter le dataset en JSONL ou JSON."""
    content, filename = await export_dataset(
        current_user, org_id, dataset_id, fmt=format,
    )
    media_type = "application/json" if format == "json" else "application/x-ndjson"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
