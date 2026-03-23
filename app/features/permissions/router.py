"""
Routes permissions sur les dossiers.
Toutes les routes nécessitent une authentification.
"""

from fastapi import APIRouter

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.permissions.schemas import (
    CascadeImpact,
    EffectiveRight,
    FolderAccessUser,
    MemberPermissionDetail,
    MemberPermissionRead,
    MemberPermissionSet,
    TeamPermissionRead,
    TeamPermissionSet,
)
from app.features.permissions.service import (
    delete_team_permission,
    get_effective_right,
    get_folder_access_list,
    get_member_permissions_detail,
    get_team_permissions_matrix,
    get_user_effective_rights_all_folders,
    preview_cascade_impact,
    set_member_permission,
    set_team_permission,
)

router = APIRouter(
    prefix="/organizations/{org_id}/permissions",
    tags=["Permissions"],
)


# ─── US-PERM-01/02/03 : Droits d'équipe sur un dossier ──────────

@router.put("/teams", response_model=TeamPermissionRead | None)
async def set_team_perm(
    org_id: str, payload: TeamPermissionSet, current_user: CurrentUser,
):
    """
    Attribuer ou modifier les droits d'une équipe sur un dossier.
    Si can_read=false et can_write=false → supprime le droit.
    Plafonnement automatique pour les sous-équipes.
    """
    perm = await set_team_permission(
        current_user, org_id,
        payload.team_id, payload.folder_id,
        payload.can_read, payload.can_write,
    )
    return TeamPermissionRead.from_perm(perm) if perm else None


@router.delete(
    "/teams/{team_id}/folders/{folder_id}", response_model=MessageResponse,
)
async def remove_team_perm(
    org_id: str, team_id: str, folder_id: str, current_user: CurrentUser,
):
    """Supprimer les droits d'une équipe sur un dossier + cascade."""
    await delete_team_permission(current_user, org_id, team_id, folder_id)
    return MessageResponse(message="Droits supprimés")


# ─── US-PERM-04 : Droits individuels d'un membre ────────────────

@router.put("/members", response_model=MemberPermissionRead | None)
async def set_member_perm(
    org_id: str, payload: MemberPermissionSet, current_user: CurrentUser,
):
    """
    Attribuer ou modifier les droits individuels d'un membre.
    Plafonnés par les droits de l'équipe sur le dossier.
    """
    perm = await set_member_permission(
        current_user, org_id,
        payload.user_id, payload.team_id, payload.folder_id,
        payload.can_read, payload.can_write,
    )
    return MemberPermissionRead.from_perm(perm) if perm else None


# ─── US-PERM-05 : Consulter les droits d'un membre ──────────────

@router.get(
    "/teams/{team_id}/members/{user_id}",
    response_model=list[MemberPermissionDetail],
)
async def get_member_perms(
    org_id: str, team_id: str, user_id: str, current_user: CurrentUser,
):
    """
    Visualiser les droits d'un membre : droit équipe + droit individuel
    pour chaque dossier.
    """
    details = await get_member_permissions_detail(
        current_user, org_id, team_id, user_id,
    )
    return [MemberPermissionDetail(**d) for d in details]


# ─── US-PERM-06 : Droit effectif d'un utilisateur sur un dossier ─

@router.get(
    "/effective/users/{user_id}/folders/{folder_id}",
    response_model=EffectiveRight,
)
async def effective_right(
    org_id: str, user_id: str, folder_id: str, current_user: CurrentUser,
):
    """Calcul du droit effectif d'un utilisateur sur un dossier."""
    from app.features.folders.models import Folder

    right = await get_effective_right(user_id, folder_id)
    folder = await Folder.get(folder_id)
    return EffectiveRight(
        folder_id=folder_id,
        folder_name=folder.name if folder else "?",
        can_read=right["can_read"],
        can_write=right["can_write"],
        sources=right["sources"],
    )


# ─── US-PERM-08 : Matrice droits d'une équipe ───────────────────

@router.get(
    "/teams/{team_id}/matrix",
    response_model=list[TeamPermissionRead],
)
async def team_matrix(
    org_id: str, team_id: str, current_user: CurrentUser,
):
    """Matrice des droits d'une équipe sur tous les dossiers."""
    perms = await get_team_permissions_matrix(current_user, org_id, team_id)
    return perms


# ─── US-PERM-09 : Droits effectifs d'un utilisateur (tous dossiers)

@router.get(
    "/effective/users/{user_id}",
    response_model=list[EffectiveRight],
)
async def user_effective_rights(
    org_id: str, user_id: str, current_user: CurrentUser,
):
    """Droits effectifs d'un utilisateur sur tous les dossiers de l'org."""
    rights = await get_user_effective_rights_all_folders(user_id, org_id)
    return [EffectiveRight(**r) for r in rights]


# ─── US-PERM-10 : Qui a accès à un dossier ──────────────────────

@router.get(
    "/folders/{folder_id}/access",
    response_model=list[FolderAccessUser],
)
async def folder_access(
    org_id: str, folder_id: str, current_user: CurrentUser,
):
    """Liste des utilisateurs ayant accès à un dossier avec leur droit effectif."""
    users = await get_folder_access_list(current_user, org_id, folder_id)
    return [FolderAccessUser(**u) for u in users]


# ─── US-PERM-11 : Preview impact cascade ─────────────────────────

@router.get(
    "/teams/{team_id}/folders/{folder_id}/cascade-impact",
    response_model=CascadeImpact,
)
async def cascade_impact(
    org_id: str, team_id: str, folder_id: str, current_user: CurrentUser,
):
    """
    Prévisualise l'impact d'une suppression de droit d'équipe
    sur les sous-équipes et membres (avant confirmation).
    """
    impact = await preview_cascade_impact(team_id, folder_id)
    return CascadeImpact(**impact)
