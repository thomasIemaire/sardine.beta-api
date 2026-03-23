"""
Routes dossiers & corbeille.
Toutes les routes nécessitent une authentification.
L'org_id est passé en paramètre de route pour le contexte organisationnel.
"""

from fastapi import APIRouter

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.folders.schemas import (
    BreadcrumbItem,
    FolderCreate,
    FolderMove,
    FolderRead,
    FolderRename,
    TrashItemRead,
)
from app.features.folders.service import (
    create_folder,
    empty_trash,
    get_breadcrumb,
    get_folder_contents,
    get_trash_contents,
    move_folder,
    rename_folder,
    restore_folder,
    soft_delete_folder,
)

router = APIRouter(prefix="/organizations/{org_id}/folders", tags=["Folders"])


# ─── CRUD dossiers ────────────────────────────────────────────────

@router.post("/", response_model=FolderRead, status_code=201)
async def create(org_id: str, payload: FolderCreate, current_user: CurrentUser):
    """Créer un sous-dossier dans un dossier existant."""
    folder = await create_folder(org_id, payload)
    return FolderRead.from_folder(folder)


@router.patch("/{folder_id}/rename", response_model=FolderRead)
async def rename(folder_id: str, payload: FolderRename, current_user: CurrentUser):
    """Renommer un dossier (hors racine et corbeille)."""
    folder = await rename_folder(folder_id, payload)
    return FolderRead.from_folder(folder)


@router.get("/{folder_id}/contents", response_model=list[FolderRead])
async def contents(org_id: str, folder_id: str, current_user: CurrentUser):
    """Lister les sous-dossiers d'un dossier."""
    folders = await get_folder_contents(org_id, folder_id)
    return [FolderRead.from_folder(f) for f in folders]


@router.get("/{folder_id}/breadcrumb", response_model=list[BreadcrumbItem])
async def breadcrumb(folder_id: str, current_user: CurrentUser):
    """Fil d'Ariane du dossier courant."""
    return await get_breadcrumb(folder_id)


@router.delete("/{folder_id}", response_model=MessageResponse)
async def delete(folder_id: str, current_user: CurrentUser):
    """Supprimer un dossier (déplacement en corbeille)."""
    await soft_delete_folder(str(current_user.id), folder_id)
    return MessageResponse(message="Dossier déplacé dans la corbeille")


@router.patch("/{folder_id}/move", response_model=FolderRead)
async def move(folder_id: str, payload: FolderMove, current_user: CurrentUser):
    """Déplacer un dossier vers un autre dossier."""
    folder = await move_folder(folder_id, payload)
    return FolderRead.from_folder(folder)


# ─── Corbeille ────────────────────────────────────────────────────

@router.get("/trash", response_model=list[TrashItemRead])
async def trash_contents(org_id: str, current_user: CurrentUser):
    """Consulter le contenu de la corbeille."""
    items = await get_trash_contents(org_id)
    return [TrashItemRead.from_folder(i) for i in items]


@router.post("/{folder_id}/restore", response_model=FolderRead)
async def restore(folder_id: str, current_user: CurrentUser):
    """Restaurer un élément depuis la corbeille."""
    folder = await restore_folder(folder_id)
    return FolderRead.from_folder(folder)


@router.delete("/trash/empty", response_model=MessageResponse)
async def empty_trash_route(org_id: str, current_user: CurrentUser):
    """Vider intégralement la corbeille (irréversible)."""
    count = await empty_trash(str(current_user.id), org_id)
    return MessageResponse(message=f"{count} élément(s) supprimé(s) définitivement")
