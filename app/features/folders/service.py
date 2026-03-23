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


async def create_folder(org_id: str, payload: FolderCreate) -> Folder:
    """
    Créer un sous-dossier.
    Le nom doit être unique dans le même dossier parent.
    """
    parent = await Folder.get(PydanticObjectId(payload.parent_id))
    if not parent:
        raise NotFoundError("Dossier parent non trouvé")
    if str(parent.organization_id) != org_id:
        raise ForbiddenError("Le dossier parent n'appartient pas à cette organisation")

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


async def get_folder_contents(org_id: str, folder_id: str) -> list[Folder]:
    """
    Liste les sous-dossiers d'un dossier donné.
    Exclut les éléments supprimés (dans la corbeille).
    """
    return await Folder.find(
        Folder.organization_id == PydanticObjectId(org_id),
        Folder.parent_id == PydanticObjectId(folder_id),
        Folder.deleted_at == None,  # noqa: E711
    ).sort("name").to_list()


async def get_breadcrumb(folder_id: str) -> list[dict]:
    """
    Construit le fil d'Ariane (breadcrumb) en remontant
    les parents jusqu'au dossier racine.
    """
    breadcrumb = []
    current = await Folder.get(PydanticObjectId(folder_id))
    seen: set[str] = set()
    max_depth = 100

    # Remontee recursive vers le dossier racine (avec protection anti-boucle)
    while current and len(breadcrumb) < max_depth:
        cid = str(current.id)
        if cid in seen:
            break  # Cycle detecte
        seen.add(cid)
        breadcrumb.append({"id": cid, "name": current.name})
        if current.parent_id:
            current = await Folder.get(current.parent_id)
        else:
            break

    # Inverser pour avoir racine → dossier courant
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
    Protections :
      - Pas de déplacement d'un dossier dans ses propres enfants (boucle)
      - Unicité du nom dans le nouveau parent
    """
    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouvé")
    if folder.is_root or folder.is_trash:
        raise ForbiddenError("Les dossiers système ne peuvent pas être déplacés")

    target = await Folder.get(PydanticObjectId(payload.target_parent_id))
    if not target:
        raise NotFoundError("Dossier de destination non trouvé")

    # Protection anti-boucle : le dossier ne peut pas être déplacé dans un de ses descendants
    descendants = await _get_all_descendants(folder.id)
    if payload.target_parent_id in descendants:
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
