"""
Routes gestion des fichiers.
Toutes les routes necessitent une authentification.
Les droits d'acces (lecture/ecriture) sont verifies dans le service.
"""

from fastapi import APIRouter, Query, UploadFile
from fastapi.responses import FileResponse

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.files.schemas import (
    BulkDeleteRequest,
    BulkDeleteResult,
    FileMove,
    FileRead,
    FileRename,
    FileVersionRead,
    TrashFileRead,
    UploadResult,
)
from app.features.files.service import (
    bulk_delete,
    get_download_path,
    get_file_detail,
    list_folder_files,
    list_trash_files,
    list_versions,
    move_file,
    rename_file,
    restore_file,
    restore_version,
    soft_delete_file,
    upload_file,
    upload_files,
    upload_new_version,
)

router = APIRouter(
    prefix="/organizations/{org_id}/files",
    tags=["Files"],
)


# ─── US-FILE-01 : Depot d'un fichier ────────────────────────────

@router.post(
    "/folders/{folder_id}/upload",
    response_model=FileRead,
    status_code=201,
)
async def upload_single(
    org_id: str, folder_id: str,
    file: UploadFile, current_user: CurrentUser,
):
    """Deposer un fichier dans un dossier (ecriture requise)."""
    f = await upload_file(current_user, org_id, folder_id, file)
    return FileRead.from_file(f)


# ─── US-FILE-02 : Depot multiple ────────────────────────────────

@router.post(
    "/folders/{folder_id}/upload-multiple",
    response_model=UploadResult,
    status_code=201,
)
async def upload_multiple(
    org_id: str, folder_id: str,
    files: list[UploadFile], current_user: CurrentUser,
):
    """Deposer plusieurs fichiers en une seule operation."""
    result = await upload_files(current_user, org_id, folder_id, files)
    return UploadResult(
        success=[FileRead.from_file(f) for f in result["success"]],
        errors=result["errors"],
    )


# ─── Listing des fichiers d'un dossier ──────────────────────────

@router.get(
    "/folders/{folder_id}",
)
async def list_files(
    org_id: str, folder_id: str, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, description="Recherche par nom"),
    mime_type: str | None = Query(None, description="Ex: application/pdf, image/*"),
    sort_by: str = Query("name", pattern="^(name|size|created_at|updated_at)$"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
):
    """
    Liste des fichiers d'un dossier (pagine, filtrable, triable).
    Filtres : search (nom), mime_type.
    Tri : name, size, created_at, updated_at (asc/desc).
    """
    result = await list_folder_files(
        current_user, org_id, folder_id,
        page=page, page_size=page_size,
        search=search, mime_type=mime_type,
        sort_by=sort_by, sort_order=sort_order,
    )
    return {
        "items": [FileRead.from_file(f) for f in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


# ─── US-FILE-10 : Detail / previsualisation ─────────────────────

@router.get("/{file_id}", response_model=FileRead)
async def get_detail(
    org_id: str, file_id: str, current_user: CurrentUser,
):
    """Detail d'un fichier avec metadonnees (lecture suffit)."""
    f = await get_file_detail(current_user, org_id, file_id)
    return FileRead.from_file(f)


# ─── US-FILE-04 : Renommer ──────────────────────────────────────

@router.patch("/{file_id}/rename", response_model=FileRead)
async def rename(
    org_id: str, file_id: str,
    payload: FileRename, current_user: CurrentUser,
):
    """Renommer un fichier (ecriture requise)."""
    f = await rename_file(current_user, org_id, file_id, payload.name)
    return FileRead.from_file(f)


# ─── US-FILE-06 : Deplacer ──────────────────────────────────────

@router.patch("/{file_id}/move", response_model=FileRead)
async def move(
    org_id: str, file_id: str,
    payload: FileMove, current_user: CurrentUser,
):
    """Deplacer un fichier (ecriture sur source ET cible)."""
    f = await move_file(
        current_user, org_id, file_id, payload.target_folder_id,
    )
    return FileRead.from_file(f)


# ─── US-FILE-05 : Nouvelle version ──────────────────────────────

@router.post(
    "/{file_id}/versions",
    response_model=FileVersionRead,
    status_code=201,
)
async def new_version(
    org_id: str, file_id: str,
    file: UploadFile, current_user: CurrentUser,
):
    """Deposer une nouvelle version d'un fichier existant."""
    v = await upload_new_version(current_user, org_id, file_id, file)
    return FileVersionRead.from_version(v)


# ─── US-FILE-07 : Historique des versions ────────────────────────

@router.get(
    "/{file_id}/versions",
    response_model=list[FileVersionRead],
)
async def get_versions(
    org_id: str, file_id: str, current_user: CurrentUser,
):
    """Liste des versions d'un fichier (lecture suffit)."""
    versions = await list_versions(current_user, org_id, file_id)
    return [FileVersionRead.from_version(v) for v in versions]


@router.post(
    "/{file_id}/versions/{version_id}/restore",
    response_model=FileVersionRead,
)
async def restore_ver(
    org_id: str, file_id: str, version_id: str,
    current_user: CurrentUser,
):
    """Restaurer une version anterieure comme nouvelle version courante."""
    v = await restore_version(current_user, org_id, file_id, version_id)
    return FileVersionRead.from_version(v)


# ─── US-FILE-11 : Telechargement ────────────────────────────────

@router.get("/{file_id}/download")
async def download(
    org_id: str, file_id: str, current_user: CurrentUser,
    version_id: str | None = Query(None),
):
    """
    Telecharger un fichier (version courante ou specifique).
    Lecture suffit.
    """
    path, filename = await get_download_path(
        current_user, org_id, file_id, version_id,
    )
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


# ─── US-FILE-08 : Suppression ───────────────────────────────────

@router.delete("/{file_id}", response_model=MessageResponse)
async def delete_file(
    org_id: str, file_id: str, current_user: CurrentUser,
):
    """Supprimer un fichier (corbeille, 30j de retention)."""
    await soft_delete_file(current_user, org_id, file_id)
    return MessageResponse(message="Fichier deplace dans la corbeille")


# ─── US-FILE-09 : Suppression en masse ──────────────────────────

@router.post("/bulk-delete", response_model=BulkDeleteResult)
async def bulk_del(
    org_id: str, payload: BulkDeleteRequest, current_user: CurrentUser,
):
    """
    Supprimer plusieurs fichiers et/ou dossiers en une seule operation.
    Les elements sans droit ou proteges sont ignores et signales.
    """
    result = await bulk_delete(
        current_user, org_id, payload.file_ids, payload.folder_ids,
    )
    return BulkDeleteResult(**result)


# ─── Corbeille fichiers ─────────────────────────────────────────

@router.get("/trash/list", response_model=list[TrashFileRead])
async def trash_files(org_id: str, current_user: CurrentUser):
    """Liste des fichiers dans la corbeille."""
    files = await list_trash_files(org_id)
    return [TrashFileRead.from_file(f) for f in files]


@router.post("/{file_id}/restore", response_model=FileRead)
async def restore_from_trash(
    org_id: str, file_id: str, current_user: CurrentUser,
):
    """Restaurer un fichier depuis la corbeille."""
    f = await restore_file(current_user, org_id, file_id)
    return FileRead.from_file(f)


# ─── Commentaires ────────────────────────────────────────────────

@router.post("/{file_id}/comments", status_code=201)
async def add_file_comment(
    org_id: str, file_id: str,
    payload: dict, current_user: CurrentUser,
):
    """Ajouter un commentaire sur un fichier (lecture suffit)."""
    from app.features.files.comments import CommentRead
    from app.features.files.comments import add_comment as _add

    comment = await _add(current_user, file_id, payload["content"])
    return CommentRead.from_comment(comment, current_user)


@router.get("/{file_id}/comments")
async def get_comments(
    org_id: str, file_id: str, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Liste des commentaires d'un fichier (pagine)."""
    from app.features.files.comments import list_comments

    return await list_comments(current_user, file_id, page, page_size)


@router.delete("/comments/{comment_id}", response_model=MessageResponse)
async def remove_comment(
    org_id: str, comment_id: str, current_user: CurrentUser,
):
    """Supprimer un commentaire (auteur uniquement)."""
    from app.features.files.comments import delete_comment

    await delete_comment(current_user, comment_id)
    return MessageResponse(message="Commentaire supprime")
