"""
Service permissions — gestion des droits d'accès sur les dossiers.

Implémente les US-PERM-01 à US-PERM-13 :
  - Attribution de droits équipe et individuel avec plafonnement
  - Calcul du droit effectif (union, le plus permissif gagne)
  - Cascade lors de la réduction d'un droit parent
  - Nettoyage lors du retrait d'un membre ou suppression d'un dossier
  - Visualisation et audit des droits
"""

from datetime import UTC, datetime

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.enums import Status, TeamMemberRole
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.auth.models import User
from app.features.folders.models import Folder
from app.features.organizations.models import Organization
from app.features.permissions.models import FolderMemberPermission, FolderTeamPermission
from app.features.teams.models import Team, TeamHierarchy, TeamMember

# ─── Helpers d'autorisation ──────────────────────────────────────

async def _is_org_owner(user: User, org_id: str) -> bool:
    """Vérifie si l'utilisateur est propriétaire de l'organisation."""
    org = await Organization.get(PydanticObjectId(org_id))
    return org is not None and str(org.owner_id) == str(user.id)


async def _get_org(org_id: str) -> Organization:
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    return org


async def _get_folder(folder_id: str) -> Folder:
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouvé")
    return folder


async def _get_team(team_id: str) -> Team:
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")
    return team


async def _is_parent_team_owner(user: User, team: Team) -> bool:
    """
    Vérifie si l'utilisateur est propriétaire d'une équipe parente
    de l'équipe donnée (récursif).
    """
    parent_links = await TeamHierarchy.find(
        TeamHierarchy.child_team_id == team.id,
    ).to_list()

    for link in parent_links:
        # Propriétaire direct du parent ?
        direct = await TeamMember.find_one(
            TeamMember.team_id == link.parent_team_id,
            TeamMember.user_id == user.id,
            TeamMember.role == TeamMemberRole.OWNER,
            TeamMember.status == Status.ACTIVE,
        )
        if direct:
            return True
        # Propriétaire d'un ancêtre du parent ?
        parent_team = await Team.get(link.parent_team_id)
        if parent_team and await _is_parent_team_owner(user, parent_team):
            return True

    return False


async def _is_team_owner_direct(user: User, team_id: PydanticObjectId) -> bool:
    """Vérifie si l'utilisateur est propriétaire direct de l'équipe."""
    member = await TeamMember.find_one(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user.id,
        TeamMember.role == TeamMemberRole.OWNER,
        TeamMember.status == Status.ACTIVE,
    )
    return member is not None


async def _can_manage_team_permission(
    user: User, org: Organization, team: Team,
) -> None:
    """
    Vérifie si l'utilisateur peut gérer les droits d'une équipe.
    US-PERM-01: Le propriétaire de l'org peut tout.
    US-PERM-02: Le propriétaire d'une équipe parente peut gérer les sous-équipes.
    US-PERM-03: Le propriétaire de l'équipe racine peut gérer sa propre équipe.
    """
    # Propriétaire de l'organisation → toujours autorisé
    if str(org.owner_id) == str(user.id):
        return

    # Équipe racine : le propriétaire de l'équipe racine peut gérer ses propres droits
    if team.is_root:
        if await _is_team_owner_direct(user, team.id):
            return
        raise ForbiddenError(
            "Seul le propriétaire de l'organisation ou de l'équipe racine "
            "peut gérer ces droits"
        )

    # Équipe non-racine : le propriétaire de l'équipe parente peut gérer
    # (le propriétaire de sa propre équipe ne peut PAS modifier ses propres droits)
    if await _is_parent_team_owner(user, team):
        return

    raise ForbiddenError(
        "Seul le propriétaire de l'organisation ou d'une équipe parente "
        "peut gérer les droits de cette équipe"
    )


async def _can_manage_member_permission(
    user: User, org: Organization, team: Team,
) -> None:
    """
    Vérifie si l'utilisateur peut gérer les droits individuels d'un membre.
    US-PERM-04: Propriétaire de l'équipe, d'une équipe parente, ou de l'org.
    """
    if str(org.owner_id) == str(user.id):
        return

    if await _is_team_owner_direct(user, team.id):
        return

    if await _is_parent_team_owner(user, team):
        return

    raise ForbiddenError(
        "Seul le propriétaire de l'organisation, de l'équipe ou d'une "
        "équipe parente peut gérer les droits individuels"
    )


async def _get_team_permission_on_folder(
    team_id: PydanticObjectId, folder_id: PydanticObjectId,
) -> FolderTeamPermission | None:
    return await FolderTeamPermission.find_one(
        FolderTeamPermission.team_id == team_id,
        FolderTeamPermission.folder_id == folder_id,
    )


# ─── US-PERM-01/02/03 : Droits d'équipe sur un dossier ─────────

async def set_team_permission(
    user: User, org_id: str,
    team_id: str, folder_id: str,
    can_read: bool, can_write: bool,
) -> FolderTeamPermission | None:
    """
    Attribue ou modifie les droits d'une équipe sur un dossier.
    Si can_read=False et can_write=False → supprime le droit (Aucun).
    Écriture implique lecture.
    Plafonnement pour les sous-équipes (US-PERM-02).
    Retourne la permission ou None si supprimée.
    """
    org = await _get_org(org_id)
    team = await _get_team(team_id)
    folder = await _get_folder(folder_id)

    # Vérifier que l'équipe et le dossier appartiennent à la même org
    if str(team.organization_id) != org_id or str(folder.organization_id) != org_id:
        raise ValidationError("L'équipe et le dossier doivent appartenir à la même organisation")

    # Vérifier l'autorisation
    await _can_manage_team_permission(user, org, team)

    # Écriture implique lecture
    if can_write:
        can_read = True

    # Plafonnement pour sous-équipes (sauf si c'est le propriétaire de l'org)
    if str(org.owner_id) != str(user.id):
        # L'utilisateur est propriétaire d'une équipe parente → vérifier le plafond
        await _check_parent_team_ceiling(team, folder.id, can_read, can_write)

    # Si aucun droit → supprimer
    if not can_read and not can_write:
        existing = await _get_team_permission_on_folder(team.id, folder.id)
        if existing:
            await existing.delete()
            # Cascade : supprimer les droits des sous-équipes sur ce dossier
            await _cascade_remove_permissions(team.id, folder.id)
        return None

    # Créer ou mettre à jour
    existing = await _get_team_permission_on_folder(team.id, folder.id)
    if existing:
        old_write = existing.can_write
        await existing.set({
            "can_read": can_read,
            "can_write": can_write,
            "updated_at": datetime.now(UTC),
        })
        # Si le droit a été réduit → cascade (US-PERM-11)
        if old_write and not can_write:
            await _cascade_reduce_write(team.id, folder.id)
        return existing

    perm = FolderTeamPermission(
        team_id=team.id,
        folder_id=folder.id,
        can_read=can_read,
        can_write=can_write,
    )
    await perm.insert()
    return perm


async def _check_parent_team_ceiling(
    team: Team, folder_id: PydanticObjectId,
    can_read: bool, can_write: bool,
) -> None:
    """
    Vérifie que le droit demandé ne dépasse pas le plafond
    de l'équipe parente sur ce dossier.
    """
    parent_links = await TeamHierarchy.find(
        TeamHierarchy.child_team_id == team.id,
    ).to_list()

    if not parent_links:
        return  # Équipe racine, pas de plafond (géré par le propriétaire de l'org)

    # Verifier qu'au moins un parent a des droits suffisants
    # On cherche le parent le plus permissif (union des droits parents)
    best_read = False
    best_write = False

    for link in parent_links:
        parent_perm = await _get_team_permission_on_folder(
            link.parent_team_id, folder_id,
        )
        if parent_perm:
            best_read = best_read or parent_perm.can_read
            best_write = best_write or parent_perm.can_write

    # Verifier le plafond contre le meilleur droit parent
    if can_write and not best_write:
        if best_read:
            raise ValidationError(
                "Impossible d'attribuer l'ecriture : aucune equipe parente "
                "n'a le droit d'ecriture sur ce dossier"
            )
        raise ValidationError(
            "Aucune equipe parente n'a de droit sur ce dossier"
        )
    if can_read and not best_read:
        raise ValidationError(
            "Aucune equipe parente n'a de droit sur ce dossier"
        )


async def delete_team_permission(
    user: User, org_id: str, team_id: str, folder_id: str,
) -> None:
    """Supprime les droits d'une équipe sur un dossier + cascade."""
    await set_team_permission(user, org_id, team_id, folder_id, False, False)


# ─── US-PERM-04 : Droits individuels d'un membre ────────────────

async def set_member_permission(
    user: User, org_id: str,
    target_user_id: str, team_id: str, folder_id: str,
    can_read: bool, can_write: bool,
) -> FolderMemberPermission | None:
    """
    Attribue ou modifie les droits individuels d'un membre.
    Plafonnement : droit individuel ≤ droit de l'équipe sur ce dossier.
    """
    org = await _get_org(org_id)
    team = await _get_team(team_id)
    folder = await _get_folder(folder_id)

    if str(team.organization_id) != org_id or str(folder.organization_id) != org_id:
        raise ValidationError("L'équipe et le dossier doivent appartenir à la même organisation")

    # Vérifier l'autorisation
    await _can_manage_member_permission(user, org, team)

    # Vérifier que le membre appartient à l'équipe
    membership = await TeamMember.find_one(
        TeamMember.team_id == team.id,
        TeamMember.user_id == PydanticObjectId(target_user_id),
        TeamMember.status == Status.ACTIVE,
    )
    if not membership:
        raise NotFoundError("Membre non trouvé dans cette équipe")

    # Écriture implique lecture
    if can_write:
        can_read = True

    # Plafonnement : droit individuel ≤ droit de l'équipe
    team_perm = await _get_team_permission_on_folder(team.id, folder.id)
    if can_write and (not team_perm or not team_perm.can_write):
        raise ValidationError(
            "Le droit individuel ne peut pas dépasser le droit de l'équipe "
            "(l'équipe n'a pas le droit d'écriture sur ce dossier)"
        )
    if can_read and (not team_perm or not team_perm.can_read):
        raise ValidationError(
            "Le droit individuel ne peut pas dépasser le droit de l'équipe "
            "(l'équipe n'a aucun droit sur ce dossier)"
        )

    uid = PydanticObjectId(target_user_id)
    tid = team.id
    fid = folder.id

    # Si aucun droit → supprimer
    if not can_read and not can_write:
        existing = await FolderMemberPermission.find_one(
            FolderMemberPermission.user_id == uid,
            FolderMemberPermission.team_id == tid,
            FolderMemberPermission.folder_id == fid,
        )
        if existing:
            await existing.delete()
        return None

    # Créer ou mettre à jour
    existing = await FolderMemberPermission.find_one(
        FolderMemberPermission.user_id == uid,
        FolderMemberPermission.team_id == tid,
        FolderMemberPermission.folder_id == fid,
    )
    if existing:
        await existing.set({
            "can_read": can_read,
            "can_write": can_write,
            "updated_at": datetime.now(UTC),
        })
        return existing

    perm = FolderMemberPermission(
        user_id=uid,
        team_id=tid,
        folder_id=fid,
        can_read=can_read,
        can_write=can_write,
    )
    await perm.insert()
    return perm


# ─── US-PERM-05 : Droits individuels d'un membre (consultation) ─

async def get_member_permissions_detail(
    user: User, org_id: str, team_id: str, target_user_id: str,
) -> list[dict]:
    """
    Retourne pour chaque dossier : le droit de l'équipe + le droit individuel.
    """
    org = await _get_org(org_id)
    team = await _get_team(team_id)
    await _can_manage_member_permission(user, org, team)

    # Droits de l'équipe
    team_perms = await FolderTeamPermission.find(
        FolderTeamPermission.team_id == team.id,
    ).to_list()

    uid = PydanticObjectId(target_user_id)
    result = []

    for tp in team_perms:
        folder = await Folder.get(tp.folder_id)
        if not folder:
            continue

        # Droit individuel sur ce dossier ?
        mp = await FolderMemberPermission.find_one(
            FolderMemberPermission.user_id == uid,
            FolderMemberPermission.team_id == team.id,
            FolderMemberPermission.folder_id == tp.folder_id,
        )
        result.append({
            "folder_id": str(tp.folder_id),
            "folder_name": folder.name,
            "team_can_read": tp.can_read,
            "team_can_write": tp.can_write,
            "individual_can_read": mp.can_read if mp else None,
            "individual_can_write": mp.can_write if mp else None,
        })

    return result


# ─── US-PERM-06 : Calcul du droit effectif ──────────────────────

async def get_effective_right(
    user_id: str, folder_id: str,
) -> dict:
    """
    Calcule le droit effectif d'un utilisateur sur un dossier.
    Union de tous les droits (équipes + individuels) → le plus permissif gagne.
    Retourne {"can_read": bool, "can_write": bool, "sources": [...]}.
    """
    uid = PydanticObjectId(user_id)
    fid = PydanticObjectId(folder_id)

    # 1. Trouver toutes les équipes de l'utilisateur
    memberships = await TeamMember.find(
        TeamMember.user_id == uid,
        TeamMember.status == Status.ACTIVE,
    ).to_list()

    can_read = False
    can_write = False
    sources: list[dict] = []

    for m in memberships:
        # Droit de l'équipe sur ce dossier
        tp = await _get_team_permission_on_folder(m.team_id, fid)
        if tp and (tp.can_read or tp.can_write):
            team = await Team.get(m.team_id)
            team_name = team.name if team else "?"
            sources.append({
                "type": "team",
                "team_id": str(m.team_id),
                "team_name": team_name,
                "can_read": tp.can_read,
                "can_write": tp.can_write,
            })
            can_read = can_read or tp.can_read
            can_write = can_write or tp.can_write

        # Droit individuel dans le contexte de cette équipe
        mp = await FolderMemberPermission.find_one(
            FolderMemberPermission.user_id == uid,
            FolderMemberPermission.team_id == m.team_id,
            FolderMemberPermission.folder_id == fid,
        )
        if mp and (mp.can_read or mp.can_write):
            sources.append({
                "type": "individual",
                "team_id": str(m.team_id),
                "can_read": mp.can_read,
                "can_write": mp.can_write,
            })
            can_read = can_read or mp.can_read
            can_write = can_write or mp.can_write

    # Écriture implique lecture
    if can_write:
        can_read = True

    return {"can_read": can_read, "can_write": can_write, "sources": sources}


async def get_user_effective_rights_all_folders(
    user_id: str, org_id: str,
) -> list[dict]:
    """
    US-PERM-09 : Droits effectifs d'un utilisateur sur tous les dossiers de l'org.
    """
    folders = await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.deleted_at == None,  # noqa: E711
    ).to_list()

    results = []
    for folder in folders:
        right = await get_effective_right(user_id, str(folder.id))
        if right["can_read"] or right["can_write"]:
            results.append({
                "folder_id": str(folder.id),
                "folder_name": folder.name,
                "can_read": right["can_read"],
                "can_write": right["can_write"],
                "sources": right["sources"],
            })

    return results


# ─── US-PERM-07 : Contrôle d'accès ──────────────────────────────

async def check_folder_access(
    user_id: str, folder_id: str, require_write: bool = False,
) -> None:
    """
    Vérifie que l'utilisateur a le droit d'accéder au dossier.
    Lève ForbiddenError si l'accès est refusé.
    """
    right = await get_effective_right(user_id, folder_id)

    if require_write and not right["can_write"]:
        raise ForbiddenError("Vous n'avez pas le droit d'écriture sur ce dossier")

    if not right["can_read"]:
        raise ForbiddenError("Vous n'avez pas accès à ce dossier")


async def get_accessible_folder_ids(
    user_id: str, org_id: str,
) -> dict[str, dict]:
    """
    Retourne un dict folder_id → {"can_read": bool, "can_write": bool}
    pour tous les dossiers accessibles de l'org.
    """
    uid = PydanticObjectId(user_id)
    oid = PydanticObjectId(org_id)

    # Toutes les équipes de l'utilisateur
    memberships = await TeamMember.find(
        TeamMember.user_id == uid,
        TeamMember.status == Status.ACTIVE,
    ).to_list()

    team_ids = [m.team_id for m in memberships]
    if not team_ids:
        return {}

    # Tous les droits d'équipe pour ces équipes
    team_perms = await FolderTeamPermission.find(
        {"team_id": {"$in": team_ids}},
    ).to_list()

    # Tous les droits individuels
    member_perms = await FolderMemberPermission.find(
        FolderMemberPermission.user_id == uid,
        {"team_id": {"$in": team_ids}},
    ).to_list()

    # Filtrer par org (via les dossiers)
    folder_rights: dict[str, dict] = {}

    for tp in team_perms:
        fid = str(tp.folder_id)
        if fid not in folder_rights:
            folder_rights[fid] = {"can_read": False, "can_write": False}
        folder_rights[fid]["can_read"] = folder_rights[fid]["can_read"] or tp.can_read
        folder_rights[fid]["can_write"] = folder_rights[fid]["can_write"] or tp.can_write

    for mp in member_perms:
        fid = str(mp.folder_id)
        if fid not in folder_rights:
            folder_rights[fid] = {"can_read": False, "can_write": False}
        folder_rights[fid]["can_read"] = folder_rights[fid]["can_read"] or mp.can_read
        folder_rights[fid]["can_write"] = folder_rights[fid]["can_write"] or mp.can_write

    # Écriture implique lecture
    for fid in folder_rights:
        if folder_rights[fid]["can_write"]:
            folder_rights[fid]["can_read"] = True

    # Ne garder que les dossiers de cette org
    org_folders = await Folder.find(
        Folder.organization_id == oid,
        Folder.deleted_at == None,  # noqa: E711
    ).to_list()
    org_folder_ids = {str(f.id) for f in org_folders}

    return {fid: r for fid, r in folder_rights.items() if fid in org_folder_ids}


# ─── US-PERM-08 : Matrice droits d'une équipe ───────────────────

async def get_team_permissions_matrix(
    user: User, org_id: str, team_id: str,
) -> list[dict]:
    """Retourne la matrice equipe x dossiers avec les droits."""
    org = await _get_org(org_id)
    team = await _get_team(team_id)

    # Seul le propriétaire de l'org peut visualiser
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError(
            "Seul le propriétaire de l'organisation peut visualiser cette matrice"
        )

    perms = await FolderTeamPermission.find(
        FolderTeamPermission.team_id == team.id,
    ).to_list()

    result = []
    for p in perms:
        folder = await Folder.get(p.folder_id)
        if folder:
            result.append({
                "folder_id": str(p.folder_id),
                "folder_name": folder.name,
                "can_read": p.can_read,
                "can_write": p.can_write,
            })

    return result


# ─── US-PERM-10 : Qui a accès à un dossier ──────────────────────

async def get_folder_access_list(
    user: User, org_id: str, folder_id: str,
) -> list[dict]:
    """Retourne la liste des utilisateurs ayant accès à un dossier."""
    org = await _get_org(org_id)
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError("Seul le propriétaire de l'organisation peut voir cette liste")

    fid = PydanticObjectId(folder_id)

    # Toutes les permissions d'équipe sur ce dossier
    team_perms = await FolderTeamPermission.find(
        FolderTeamPermission.folder_id == fid,
    ).to_list()

    # Toutes les permissions individuelles sur ce dossier
    member_perms = await FolderMemberPermission.find(
        FolderMemberPermission.folder_id == fid,
    ).to_list()

    # Collecter les users avec accès via les équipes
    user_rights: dict[str, dict] = {}

    for tp in team_perms:
        if not tp.can_read and not tp.can_write:
            continue
        # Membres de cette équipe
        members = await TeamMember.find(
            TeamMember.team_id == tp.team_id,
            TeamMember.status == Status.ACTIVE,
        ).to_list()
        for m in members:
            uid = str(m.user_id)
            if uid not in user_rights:
                user_rights[uid] = {"can_read": False, "can_write": False, "sources": []}
            user_rights[uid]["can_read"] = user_rights[uid]["can_read"] or tp.can_read
            user_rights[uid]["can_write"] = user_rights[uid]["can_write"] or tp.can_write
            team = await Team.get(tp.team_id)
            user_rights[uid]["sources"].append({
                "type": "team",
                "team_id": str(tp.team_id),
                "team_name": team.name if team else "?",
                "can_read": tp.can_read,
                "can_write": tp.can_write,
            })

    # Ajouter les droits individuels
    for mp in member_perms:
        if not mp.can_read and not mp.can_write:
            continue
        uid = str(mp.user_id)
        if uid not in user_rights:
            user_rights[uid] = {"can_read": False, "can_write": False, "sources": []}
        user_rights[uid]["can_read"] = user_rights[uid]["can_read"] or mp.can_read
        user_rights[uid]["can_write"] = user_rights[uid]["can_write"] or mp.can_write
        user_rights[uid]["sources"].append({
            "type": "individual",
            "team_id": str(mp.team_id),
            "can_read": mp.can_read,
            "can_write": mp.can_write,
        })

    # Enrichir avec les infos utilisateur
    result = []
    for uid, rights in user_rights.items():
        if rights["can_write"]:
            rights["can_read"] = True
        u = await User.get(PydanticObjectId(uid))
        if u:
            result.append({
                "user_id": uid,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "can_read": rights["can_read"],
                "can_write": rights["can_write"],
                "sources": rights["sources"],
            })

    return result


# ─── US-PERM-11 : Cascade lors de la réduction d'un droit parent ─

async def preview_cascade_impact(
    team_id: str, folder_id: str,
) -> dict:
    """
    Calcule l'impact d'une suppression de droit (avant confirmation).
    Retourne le nombre de permissions d'équipes et de membres impactées.
    """
    tid = PydanticObjectId(team_id)
    fid = PydanticObjectId(folder_id)

    team_count = 0
    member_count = 0

    child_teams = await _get_all_child_teams(tid)
    for child_id in child_teams:
        perm = await _get_team_permission_on_folder(child_id, fid)
        if perm:
            team_count += 1

        mperms = await FolderMemberPermission.find(
            FolderMemberPermission.team_id == child_id,
            FolderMemberPermission.folder_id == fid,
        ).to_list()
        member_count += len(mperms)

    return {"team_permissions_impacted": team_count, "member_permissions_impacted": member_count}


async def _cascade_remove_permissions(
    team_id: PydanticObjectId, folder_id: PydanticObjectId,
) -> None:
    """Supprime récursivement les droits des sous-équipes et membres."""
    child_teams = await _get_all_child_teams(team_id)

    for child_id in child_teams:
        # Supprimer le droit d'équipe
        perm = await _get_team_permission_on_folder(child_id, folder_id)
        if perm:
            await perm.delete()

        # Supprimer les droits individuels des membres de cette sous-équipe
        await FolderMemberPermission.find(
            FolderMemberPermission.team_id == child_id,
            FolderMemberPermission.folder_id == folder_id,
        ).delete()

    # Supprimer aussi les droits individuels des membres de l'équipe parente
    await FolderMemberPermission.find(
        FolderMemberPermission.team_id == team_id,
        FolderMemberPermission.folder_id == folder_id,
    ).delete()

    await log_action(
        team_id, "PERMISSION_CASCADE_REMOVE",
        f"Cascade removal for folder {folder_id}",
    )


async def _cascade_reduce_write(
    team_id: PydanticObjectId, folder_id: PydanticObjectId,
) -> None:
    """
    Quand une équipe parente perd l'écriture, les sous-équipes
    et membres perdent aussi l'écriture sur ce dossier.
    """
    child_teams = await _get_all_child_teams(team_id)

    for child_id in child_teams:
        perm = await _get_team_permission_on_folder(child_id, folder_id)
        if perm and perm.can_write:
            await perm.set({
                "can_write": False,
                "updated_at": datetime.now(UTC),
            })

        # Réduire les droits individuels des membres
        await FolderMemberPermission.find(
            FolderMemberPermission.team_id == child_id,
            FolderMemberPermission.folder_id == folder_id,
            FolderMemberPermission.can_write == True,  # noqa: E712
        ).update_many({"$set": {"can_write": False, "updated_at": datetime.now(UTC)}})

    # Réduire aussi les droits individuels de l'équipe elle-même
    await FolderMemberPermission.find(
        FolderMemberPermission.team_id == team_id,
        FolderMemberPermission.folder_id == folder_id,
        FolderMemberPermission.can_write == True,  # noqa: E712
    ).update_many({"$set": {"can_write": False, "updated_at": datetime.now(UTC)}})

    await log_action(
        team_id, "PERMISSION_CASCADE_REDUCE_WRITE",
        f"Cascade write reduction for folder {folder_id}",
    )


async def _get_all_child_teams(
    team_id: PydanticObjectId,
) -> list[PydanticObjectId]:
    """Récupère récursivement tous les IDs de sous-équipes."""
    result: list[PydanticObjectId] = []
    links = await TeamHierarchy.find(
        TeamHierarchy.parent_team_id == team_id,
    ).to_list()

    for link in links:
        result.append(link.child_team_id)
        result.extend(await _get_all_child_teams(link.child_team_id))

    return result


# ─── US-PERM-12 : Nettoyage lors du retrait d'un membre ─────────

async def cleanup_member_permissions(
    user_id: str, team_id: str,
) -> int:
    """
    Supprime tous les droits individuels d'un membre
    liés à une équipe spécifique. Appelé lors du retrait/désactivation.
    Retourne le nombre de permissions supprimées.
    """
    result = await FolderMemberPermission.find(
        FolderMemberPermission.user_id == PydanticObjectId(user_id),
        FolderMemberPermission.team_id == PydanticObjectId(team_id),
    ).delete()
    return result.deleted_count if result else 0


# ─── US-PERM-13 : Nettoyage lors de la suppression d'un dossier ──

async def cleanup_folder_permissions(folder_id: str) -> int:
    """
    Supprime toutes les permissions (équipe + individuel)
    liées à un dossier. Appelé lors de la suppression définitive.
    Retourne le nombre total de permissions supprimées.
    """
    fid = PydanticObjectId(folder_id)

    r1 = await FolderTeamPermission.find(
        FolderTeamPermission.folder_id == fid,
    ).delete()

    r2 = await FolderMemberPermission.find(
        FolderMemberPermission.folder_id == fid,
    ).delete()

    count1 = r1.deleted_count if r1 else 0
    count2 = r2.deleted_count if r2 else 0
    return count1 + count2
