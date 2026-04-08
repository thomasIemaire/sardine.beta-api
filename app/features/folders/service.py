"""
Service dossiers — CRUD, navigation, corbeille et rétention.

Règles métier clés :
  - Le dossier racine et la corbeille sont des dossiers système non modifiables
  - La suppression est douce : déplacement vers la corbeille
  - Rétention de 30 jours avant purge définitive
  - Le déplacement ne doit pas créer de boucle
"""

import logging
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.features.folders.models import Folder
from app.features.folders.schemas import FolderCreate, FolderMove, FolderRename

logger = logging.getLogger(__name__)

# Durée de rétention en corbeille avant purge automatique
RETENTION_DAYS = 30


async def get_root_folder(org_id: str) -> Folder:
    """Retourne le dossier racine de l'organisation."""
    folder = await Folder.find_one(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.is_root == True,  # noqa: E712
    )
    if not folder:
        raise NotFoundError("Dossier racine introuvable")
    return folder


async def get_trash_folder(org_id: str) -> Folder:
    """Retourne le dossier corbeille de l'organisation."""
    folder = await Folder.find_one(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.is_trash == True,  # noqa: E712
    )
    if not folder:
        raise NotFoundError("Dossier corbeille introuvable")
    return folder


async def create_folder(org_id: str, payload: FolderCreate) -> Folder:
    """
    Crée un dossier.
    Si parent_id est null, le dossier est créé au niveau le plus haut
    de l'organisation (rattaché à la racine système, transparente pour
    le front).
    Le nom doit être unique dans le même dossier parent.
    """
    if payload.parent_id:
        parent = await Folder.get(PydanticObjectId(payload.parent_id))
        if not parent:
            raise NotFoundError("Dossier parent non trouvé")
        if str(parent.organization_id) != org_id:
            raise ForbiddenError("Le dossier parent n'appartient pas à cette organisation")
    else:
        # Pas de parent fourni : utiliser la racine système de l'organisation
        parent = await get_root_folder(org_id)

    # Unicité du nom dans le même parent
    existing = await Folder.find_one(
        Folder.parent_id == parent.id,
        Folder.name == payload.name,
        Folder.deleted_at == None,  # noqa: E711 — exclure les éléments supprimés
    )
    if existing:
        raise ConflictError("Un dossier avec ce nom existe déjà dans ce répertoire")

    folder = Folder(
        name=payload.name,
        organization_id=PydanticObjectId(org_id),
        parent_id=parent.id,
    )
    await folder.insert()
    return folder


async def rename_folder(folder_id: str, payload: FolderRename) -> Folder:
    """
    Renommer un dossier.
    Le dossier racine et la corbeille ne sont pas renommables.
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouvé")

    # Dossiers système protégés
    if folder.is_root or folder.is_trash:
        raise ForbiddenError("Les dossiers système ne peuvent pas être renommés")

    # Unicité du nouveau nom dans le même parent
    existing = await Folder.find_one(
        Folder.parent_id == folder.parent_id,
        Folder.name == payload.name,
        Folder.deleted_at == None,  # noqa: E711
        Folder.id != folder.id,
    )
    if existing:
        raise ConflictError("Un dossier avec ce nom existe déjà dans ce répertoire")

    await folder.set({
        "name": payload.name,
        "updated_at": datetime.now(timezone.utc),
    })
    return folder


async def get_folder_contents(
    org_id: str, folder_id: str, user_id: str | None = None,
) -> list[Folder]:
    """
    Liste les sous-dossiers d'un dossier donné.
    Exclut les éléments supprimés (dans la corbeille).

    Si user_id est fourni :
      - Si folder_id est la racine : retourne les "dossiers de plus haut niveau
        accessibles" (point d'entree virtuel), peu importe leur emplacement
        reel dans l'arborescence.
      - Sinon : retourne les enfants directs filtres selon les droits.
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        return []

    # Cas standard : pas de filtrage
    if user_id is None:
        return await Folder.find(
            Folder.organization_id == PydanticObjectId(org_id),
            Folder.parent_id == PydanticObjectId(folder_id),
            Folder.deleted_at == None,  # noqa: E711
        ).sort("name").to_list()

    from app.features.organizations.models import Organization
    from app.features.permissions.service import get_accessible_folder_ids

    accessible = await get_accessible_folder_ids(user_id, org_id)

    # Owner de l'org : toujours voir l'arborescence reelle
    org = await Organization.get(PydanticObjectId(org_id))
    is_org_owner = org and str(org.owner_id) == str(user_id)

    if folder.is_root and not is_org_owner:
        # Vue virtuelle : retourner les dossiers de plus haut niveau
        # auxquels l'utilisateur a un acces direct (non implicite).
        direct_ids = {
            fid for fid, r in accessible.items()
            if not r.get("implicit")
        }
        if not direct_ids:
            return []

        all_folders = await Folder.find(
            Folder.organization_id == PydanticObjectId(org_id),
            Folder.deleted_at == None,  # noqa: E711
        ).to_list()
        folder_map = {str(f.id): f for f in all_folders}

        # Un dossier est "top-level" si aucun de ses ancetres n'est
        # aussi accessible directement.
        top_level: list[Folder] = []
        for fid in direct_ids:
            f = folder_map.get(fid)
            if not f:
                continue
            # Verifier les ancetres
            current = f
            has_accessible_ancestor = False
            while current and current.parent_id:
                parent_id = str(current.parent_id)
                if parent_id in direct_ids:
                    has_accessible_ancestor = True
                    break
                current = folder_map.get(parent_id)
            if not has_accessible_ancestor:
                top_level.append(f)

        top_level.sort(key=lambda x: x.name)
        return top_level

    # Cas standard : enfants directs filtres
    folders = await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.parent_id == PydanticObjectId(folder_id),
        Folder.deleted_at == None,  # noqa: E711
    ).sort("name").to_list()
    return [f for f in folders if str(f.id) in accessible]


async def list_top_level_folders(org_id: str, user_id: str) -> list[Folder]:
    """
    Liste les dossiers de plus haut niveau accessibles a l'utilisateur.

    Cas membre standard : retourne ses dossiers accessibles "top level"
    (ceux dont aucun ancetre n'est aussi accessible directement).
    """
    from app.features.organizations.models import Organization
    from app.features.permissions.service import get_accessible_folder_ids

    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvee")

    is_org_owner = str(org.owner_id) == str(user_id)

    # Owner de l'org : enfants directs de la racine systeme
    if is_org_owner:
        root = await get_root_folder(org_id)
        return await Folder.find(
            Folder.organization_id == PydanticObjectId(org_id),
            Folder.parent_id == root.id,
            Folder.deleted_at == None,  # noqa: E711
        ).sort("name").to_list()

    # Membre standard : top-level accessibles
    accessible = await get_accessible_folder_ids(user_id, org_id)
    direct_ids = {
        fid for fid, r in accessible.items()
        if not r.get("implicit")
    }
    if not direct_ids:
        return []

    all_folders = await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.deleted_at == None,  # noqa: E711
    ).to_list()
    folder_map = {str(f.id): f for f in all_folders}

    top_level: list[Folder] = []
    for fid in direct_ids:
        f = folder_map.get(fid)
        if not f:
            continue
        # Verifier qu'aucun ancetre n'est aussi accessible
        current = f
        has_accessible_ancestor = False
        while current and current.parent_id:
            parent_id = str(current.parent_id)
            if parent_id in direct_ids:
                has_accessible_ancestor = True
                break
            current = folder_map.get(parent_id)
        if not has_accessible_ancestor:
            top_level.append(f)

    top_level.sort(key=lambda x: x.name)
    return top_level


async def get_breadcrumb(folder_id: str) -> list[dict]:
    """
    Construit le fil d'Ariane (breadcrumb) en remontant
    les parents jusqu'au dossier racine.
    """
    breadcrumb = []
    current = await Folder.get(PydanticObjectId(folder_id))
    seen: set[str] = set()
    max_depth = 100

    # Remontee recursive (avec protection anti-boucle).
    # On exclut la racine systeme : elle n'est pas exposee au front.
    while current and len(breadcrumb) < max_depth:
        cid = str(current.id)
        if cid in seen:
            break  # Cycle detecte
        seen.add(cid)
        if not current.is_root:
            breadcrumb.append({"id": cid, "name": current.name})
        if current.parent_id:
            current = await Folder.get(current.parent_id)
        else:
            break

    # Inverser pour avoir le plus haut → dossier courant
    breadcrumb.reverse()
    return breadcrumb


async def soft_delete_folder(user_id: str, folder_id: str) -> Folder:
    """
    Suppression douce — déplace le dossier dans la corbeille.
    Sauvegarde le parent d'origine pour permettre la restauration.
    Le dossier racine ne peut pas être supprimé.
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouvé")
    if folder.is_root or folder.is_trash:
        raise ForbiddenError("Les dossiers système ne peuvent pas être supprimés")

    # Trouver le dossier corbeille de l'organisation
    trash = await Folder.find_one(
        Folder.organization_id == folder.organization_id,
        Folder.is_trash == True,
    )

    now = datetime.now(timezone.utc)
    await folder.set({
        "deleted_at": now,
        "original_parent_id": folder.parent_id,  # Sauvegarde pour restauration
        "parent_id": trash.id,                    # Déplacé dans la corbeille
        "updated_at": now,
    })

    await log_action(user_id, "FOLDER_DELETE", f"Folder '{folder.name}' moved to trash")
    return folder


async def _get_all_descendants(folder_id: PydanticObjectId) -> set[str]:
    """
    Récupère récursivement tous les IDs descendants d'un dossier.
    Utilisé par move_folder pour détecter les boucles.
    """
    descendants = set()
    children = await Folder.find(Folder.parent_id == folder_id).to_list()
    for child in children:
        descendants.add(str(child.id))
        descendants.update(await _get_all_descendants(child.id))
    return descendants


async def move_folder(folder_id: str, payload: FolderMove) -> Folder:
    """
    Déplacer un dossier vers un nouveau parent.
    Si target_parent_id est null, le dossier est déplacé au niveau le plus haut
    (rattaché à la racine système).
    Protections :
      - Pas de déplacement d'un dossier dans ses propres enfants (boucle)
      - Unicité du nom dans le nouveau parent
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouvé")
    if folder.is_root or folder.is_trash:
        raise ForbiddenError("Les dossiers système ne peuvent pas être déplacés")

    if payload.target_parent_id:
        target = await Folder.get(PydanticObjectId(payload.target_parent_id))
        if not target:
            raise NotFoundError("Dossier de destination non trouvé")
    else:
        # Pas de cible : remonter au niveau le plus haut (racine système)
        target = await get_root_folder(str(folder.organization_id))

    # Protection anti-boucle : le dossier ne peut pas être déplacé dans un de ses descendants
    descendants = await _get_all_descendants(folder.id)
    if str(target.id) in descendants:
        raise ValidationError("Impossible : le dossier de destination est un sous-dossier")

    # Unicité du nom dans le nouveau parent
    existing = await Folder.find_one(
        Folder.parent_id == target.id,
        Folder.name == folder.name,
        Folder.deleted_at == None,  # noqa: E711
        Folder.id != folder.id,
    )
    if existing:
        raise ConflictError("Un dossier avec ce nom existe déjà dans le dossier de destination")

    await folder.set({
        "parent_id": target.id,
        "updated_at": datetime.now(timezone.utc),
    })
    return folder


# ─── Corbeille ────────────────────────────────────────────────────

async def get_trash_contents(org_id: str) -> list[Folder]:
    """Contenu de la corbeille, trié du plus récent au plus ancien."""
    return await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.deleted_at != None,  # noqa: E711 — seuls les éléments supprimés
    ).sort("-deleted_at").to_list()


async def restore_folder(folder_id: str) -> Folder:
    """
    Restaurer un élément depuis la corbeille.
    L'élément est replacé dans son dossier d'origine s'il existe.
    Si le parent d'origine a été supprimé, restauration dans le dossier racine.
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder or not folder.deleted_at:
        raise NotFoundError("Élément non trouvé dans la corbeille")

    # Vérifier si le dossier d'origine existe encore
    restore_parent_id = folder.original_parent_id
    if restore_parent_id:
        original = await Folder.get(restore_parent_id)
        # Si le parent a été supprimé → restaurer dans le dossier racine
        if not original or original.deleted_at:
            root = await Folder.find_one(
                Folder.organization_id == folder.organization_id,
                Folder.is_root == True,
            )
            restore_parent_id = root.id

    now = datetime.now(timezone.utc)
    await folder.set({
        "deleted_at": None,             # Réinitialisation du compteur de rétention
        "original_parent_id": None,
        "parent_id": restore_parent_id,
        "updated_at": now,
    })
    return folder


async def purge_expired_trash() -> int:
    """
    Suppression définitive des éléments dont la rétention
    de 30 jours est expirée. Appelé périodiquement par un background task.
    Retourne le nombre d'éléments purgés.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    # Tous les éléments supprimés avant la date limite
    expired = await Folder.find(
        Folder.deleted_at != None,  # noqa: E711
        Folder.deleted_at <= cutoff,
    ).to_list()

    count = len(expired)
    for folder in expired:
        # US-PERM-13 : nettoyage des permissions associées
        from app.features.permissions.service import cleanup_folder_permissions
        await cleanup_folder_permissions(str(folder.id))
        await folder.delete()

    if count > 0:
        logger.info("TRASH_PURGE: %d items permanently deleted", count)

    return count


async def empty_trash(user_id: str, org_id: str) -> int:
    """
    Vidage manuel de la corbeille par le propriétaire.
    Suppression définitive et irréversible de tous les éléments.
    """
    items = await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.deleted_at != None,  # noqa: E711
    ).to_list()

    count = len(items)
    for item in items:
        # US-PERM-13 : nettoyage des permissions associées
        from app.features.permissions.service import cleanup_folder_permissions
        await cleanup_folder_permissions(str(item.id))
        await item.delete()

    await log_action(user_id, "TRASH_EMPTY", f"Trash emptied: {count} items deleted")
    return count
