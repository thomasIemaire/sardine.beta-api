"""
Routes utilisateurs — profil et administration.
Les routes /admin/* nécessitent le rôle Administrateur.
"""

from fastapi import APIRouter, Query

from app.features.auth.dependencies import CurrentAdmin, CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.users.schemas import (
    AdminUserUpdate,
    BulkCreateRequest,
    BulkCreateResponse,
    BulkCreateResultItem,
    UserRead,
    UserUpdate,
)
from app.features.users.service import (
    admin_update_user,
    bulk_create_users,
    list_users,
    update_profile,
)

router = APIRouter(prefix="/users", tags=["Users"])


# ─── Profil utilisateur ───────────────────────────────────────────

@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser):
    """Consultation du profil courant."""
    return UserRead.from_user(current_user)


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    payload: UserUpdate,
    current_user: CurrentUser,
):
    """Modification du profil (nom, prénom, email)."""
    user = await update_profile(current_user, payload)
    return UserRead.from_user(user)


@router.post("/me/refresh-avatar", response_model=UserRead)
async def refresh_avatar(current_user: CurrentUser):
    """Régénère l'avatar de l'utilisateur courant (nouveau gradient aléatoire)."""
    from app.core.avatar import generate_avatar

    avatar_path = generate_avatar(str(current_user.id))
    await current_user.set({"avatar_path": avatar_path})
    return UserRead.from_user(current_user)


# ─── Administration ───────────────────────────────────────────────

@router.get("/admin/list")
async def admin_list_users(
    current_admin: CurrentAdmin,
    search: str | None = Query(None, description="Recherche nom/email"),
    status: int | None = Query(None, description="Filtre statut (0/1)"),
    role: int | None = Query(None, description="Filtre rôle (0/1)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    Liste paginée des utilisateurs.
    Filtres : recherche nom/email, statut, rôle.
    """
    result = await list_users(search, status, role, page, page_size)

    return {
        "items": [UserRead.from_user(u) for u in result["items"]],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "total_pages": result["total_pages"],
    }


@router.patch("/admin/{user_id}", response_model=UserRead)
async def admin_update_user_route(
    user_id: str,
    payload: AdminUserUpdate,
    current_admin: CurrentAdmin,
):
    """Modifier le statut ou le rôle d'un utilisateur."""
    user = await admin_update_user(current_admin, user_id, payload)
    return UserRead.from_user(user)


@router.post("/admin/bulk", response_model=BulkCreateResponse, status_code=201)
async def admin_bulk_create(
    payload: BulkCreateRequest,
    current_admin: CurrentAdmin,
):
    """
    Création massive de comptes utilisateurs.
    Pour chaque utilisateur : crée le compte, l'org privée,
    et l'ajoute comme membre des organisations spécifiées.
    Les erreurs individuelles n'arrêtent pas le traitement global.
    """
    results = await bulk_create_users(current_admin, payload.users)

    created = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])

    return BulkCreateResponse(
        total=len(results),
        created=created,
        failed=failed,
        results=[BulkCreateResultItem(**r) for r in results],
    )
