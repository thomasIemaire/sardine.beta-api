"""
Service fichiers — depot, modification, suppression, versioning, telechargement.

Stockage physique : storage/files/{org_id}/{file_id}/v{version}.{ext}
Metadonnees en MongoDB (File + FileVersion).
"""

import mimetypes
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from beanie import PydanticObjectId
from fastapi import UploadFile

from app.core.audit import log_action
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.auth.models import User
from app.features.files.models import File, FileVersion
from app.features.folders.models import Folder
from app.features.organizations.models import Organization
from app.features.permissions.service import check_folder_access

STORAGE_DIR = Path("storage/files")
RETENTION_DAYS = 30


# ─── Helpers ─────────────────────────────────────────────────────

def _get_extension(filename: str) -> str:
    """Extrait l'extension d'un nom de fichier (avec le point)."""
    return Path(filename).suffix.lower()



async def _resolve_duplicate_name(
    name: str, folder_id: PydanticObjectId, exclude_id: PydanticObjectId | None = None,
) -> str:
    """
    Si un fichier du meme nom existe deja dans le dossier,
    ajoute un suffixe numerique : document(1).pdf, document(2).pdf, etc.
    """
    base = Path(name)
    stem = base.stem
    suffix = base.suffix

    candidate = name
    counter = 1

    while True:
        query = {
            "name": candidate,
            "folder_id": folder_id,
            "deleted_at": None,
        }
        existing = await File.find_one(query)
        if existing is None or (exclude_id and existing.id == exclude_id):
            return candidate
        candidate = f"{stem}({counter}){suffix}"
        counter += 1
        if counter > 100:
            raise ValidationError("Trop de fichiers avec le meme nom")


def _storage_path(org_id: str, file_id: str, version: int, ext: str) -> str:
    """Construit le chemin de stockage pour une version."""
    return f"files/{org_id}/{file_id}/v{version}{ext}"


def _full_path(relative: str) -> Path:
    """Chemin absolu depuis la racine storage."""
    return Path("storage") / relative


# ─── US-FILE-01/02 : Depot de fichiers ───────────────────────────

async def upload_file(
    user: User, org_id: str, folder_id: str, upload: UploadFile,
) -> File:
    """
    Depose un fichier dans un dossier.
    Cree le File + la premiere FileVersion + stocke le fichier physique.
    """
    # Verifier le droit d'ecriture
    await check_folder_access(str(user.id), folder_id, require_write=True)

    folder = await Folder.get(PydanticObjectId(folder_id))
    if not folder:
        raise NotFoundError("Dossier non trouve")
    if str(folder.organization_id) != org_id:
        raise NotFoundError("Dossier non trouve dans cette organisation")

    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvee")

    # Lire le contenu du fichier
    content = await upload.read()
    file_size = len(content)

    filename = upload.filename or "sans_nom"
    ext = _get_extension(filename)
    mime = upload.content_type or mimetypes.guess_type(filename)[0] or ""

    # Gestion des doublons
    resolved_name = await _resolve_duplicate_name(
        filename, folder.id,
    )

    # Creer le document File
    file_doc = File(
        name=resolved_name,
        folder_id=folder.id,
        organization_id=PydanticObjectId(org_id),
        current_version=1,
        mime_type=mime,
        size=file_size,
        uploaded_by=user.id,
    )
    await file_doc.insert()

    # Stocker le fichier physique
    rel_path = _storage_path(org_id, str(file_doc.id), 1, ext)
    full = _full_path(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)

    # Creer la premiere version
    version = FileVersion(
        file_id=file_doc.id,
        version_number=1,
        storage_path=rel_path,
        original_name=filename,
        mime_type=mime,
        size=file_size,
        uploaded_by=user.id,
    )
    await version.insert()

    await log_action(
        user.id, "FILE_UPLOAD",
        f"File '{resolved_name}' uploaded to folder {folder_id}",
    )
    return file_doc


async def upload_files(
    user: User, org_id: str, folder_id: str, uploads: list[UploadFile],
) -> dict:
    """
    US-FILE-02 : Depot multiple. Chaque fichier est traite independamment.
    Retourne {"success": [...], "errors": [...]}.
    """
    success = []
    errors = []

    for upload in uploads:
        try:
            file_doc = await upload_file(user, org_id, folder_id, upload)
            success.append(file_doc)
        except Exception as e:
            errors.append({
                "filename": upload.filename or "?",
                "error": str(e.detail) if hasattr(e, "detail") else str(e),
            })

    return {"success": success, "errors": errors}


# ─── US-FILE-04 : Renommer un fichier ────────────────────────────

async def rename_file(
    user: User, org_id: str, file_id: str, new_name: str,
) -> File:
    """Renomme un fichier. Unicite dans le dossier parent."""
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id), require_write=True)

    resolved = await _resolve_duplicate_name(
        new_name, file_doc.folder_id, exclude_id=file_doc.id,
    )
    await file_doc.set({
        "name": resolved,
        "updated_at": datetime.now(UTC),
    })

    await log_action(user.id, "FILE_RENAME", f"File renamed to '{resolved}'")
    return file_doc


# ─── US-FILE-05 : Nouvelle version ──────────────────────────────

async def upload_new_version(
    user: User, org_id: str, file_id: str, upload: UploadFile,
) -> FileVersion:
    """
    Depose une nouvelle version d'un fichier existant.
    La version precedente est conservee dans l'historique.
    """
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id), require_write=True)

    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvee")

    content = await upload.read()
    file_size = len(content)

    filename = upload.filename or file_doc.name
    ext = _get_extension(filename)
    mime = upload.content_type or mimetypes.guess_type(filename)[0] or ""

    new_version_num = file_doc.current_version + 1

    # Stocker le fichier physique
    rel_path = _storage_path(org_id, str(file_doc.id), new_version_num, ext)
    full = _full_path(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)

    # Creer la version
    version = FileVersion(
        file_id=file_doc.id,
        version_number=new_version_num,
        storage_path=rel_path,
        original_name=filename,
        mime_type=mime,
        size=file_size,
        uploaded_by=user.id,
    )
    await version.insert()

    # Mettre a jour le fichier principal
    await file_doc.set({
        "current_version": new_version_num,
        "mime_type": mime,
        "size": file_size,
        "updated_at": datetime.now(UTC),
    })

    await log_action(
        user.id, "FILE_NEW_VERSION",
        f"File '{file_doc.name}' updated to v{new_version_num}",
    )
    return version


# ─── US-FILE-06 : Deplacer un fichier ───────────────────────────

async def move_file(
    user: User, org_id: str, file_id: str, target_folder_id: str,
) -> File:
    """Deplace un fichier vers un autre dossier (ecriture requise sur source ET cible)."""
    file_doc = await _get_file(file_id, org_id)

    # Droit d'ecriture sur le dossier source
    await check_folder_access(str(user.id), str(file_doc.folder_id), require_write=True)
    # Droit d'ecriture sur le dossier cible
    await check_folder_access(str(user.id), target_folder_id, require_write=True)

    target = await Folder.get(PydanticObjectId(target_folder_id))
    if not target:
        raise NotFoundError("Dossier cible non trouve")
    if str(target.organization_id) != org_id:
        raise NotFoundError("Dossier cible non trouve dans cette organisation")

    # Gestion des doublons dans le dossier cible
    resolved = await _resolve_duplicate_name(file_doc.name, target.id)

    old_folder_id = str(file_doc.folder_id)
    await file_doc.set({
        "folder_id": target.id,
        "name": resolved,
        "updated_at": datetime.now(UTC),
    })

    await log_action(
        user.id, "FILE_MOVE",
        f"File '{file_doc.name}' moved from {old_folder_id} to {target_folder_id}",
    )
    return file_doc


# ─── US-FILE-07 : Historique des versions ────────────────────────

async def list_versions(
    user: User, org_id: str, file_id: str,
) -> list[FileVersion]:
    """Liste toutes les versions d'un fichier (lecture suffit)."""
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id))

    return await FileVersion.find(
        FileVersion.file_id == file_doc.id,
    ).sort("-version_number").to_list()


async def get_version(
    user: User, org_id: str, file_id: str, version_id: str,
) -> FileVersion:
    """Detail d'une version specifique."""
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id))

    version = await FileVersion.get(PydanticObjectId(version_id))
    if not version or str(version.file_id) != str(file_doc.id):
        raise NotFoundError("Version non trouvee")
    return version


async def restore_version(
    user: User, org_id: str, file_id: str, version_id: str,
) -> FileVersion:
    """
    Restaure une version anterieure comme version courante.
    Cree une nouvelle version (copie) — l'historique n'est jamais ecrase.
    """
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id), require_write=True)

    old_version = await FileVersion.get(PydanticObjectId(version_id))
    if not old_version or str(old_version.file_id) != str(file_doc.id):
        raise NotFoundError("Version non trouvee")

    # Lire le contenu de l'ancienne version
    old_path = _full_path(old_version.storage_path)
    if not old_path.exists():
        raise NotFoundError("Fichier physique de cette version introuvable")

    content = old_path.read_bytes()
    ext = _get_extension(old_version.original_name)
    new_version_num = file_doc.current_version + 1

    # Stocker comme nouvelle version
    rel_path = _storage_path(org_id, str(file_doc.id), new_version_num, ext)
    full = _full_path(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)

    new_version = FileVersion(
        file_id=file_doc.id,
        version_number=new_version_num,
        storage_path=rel_path,
        original_name=old_version.original_name,
        mime_type=old_version.mime_type,
        size=old_version.size,
        uploaded_by=user.id,
    )
    await new_version.insert()

    await file_doc.set({
        "current_version": new_version_num,
        "mime_type": old_version.mime_type,
        "size": old_version.size,
        "updated_at": datetime.now(UTC),
    })

    await log_action(
        user.id, "FILE_VERSION_RESTORE",
        f"File '{file_doc.name}' restored to v{old_version.version_number} "
        f"as new v{new_version_num}",
    )
    return new_version


# ─── US-FILE-08 : Supprimer un fichier ──────────────────────────

async def soft_delete_file(
    user: User, org_id: str, file_id: str,
) -> None:
    """Suppression logique (corbeille). 30 jours de retention."""
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id), require_write=True)

    now = datetime.now(UTC)
    await file_doc.set({
        "deleted_at": now,
        "original_folder_id": file_doc.folder_id,
        "updated_at": now,
    })

    await log_action(
        user.id, "FILE_DELETE",
        f"File '{file_doc.name}' moved to trash",
    )


async def restore_file(
    user: User, org_id: str, file_id: str,
) -> File:
    """Restaure un fichier depuis la corbeille."""
    file_doc = await File.get(PydanticObjectId(file_id))
    if not file_doc or str(file_doc.organization_id) != org_id:
        raise NotFoundError("Fichier non trouve")
    if not file_doc.deleted_at:
        raise ValidationError("Ce fichier n'est pas dans la corbeille")

    # Verifier que le dossier d'origine existe encore
    restore_folder_id = file_doc.original_folder_id or file_doc.folder_id
    target_folder = await Folder.get(restore_folder_id)

    if not target_folder or target_folder.deleted_at:
        # Dossier d'origine supprime → restaurer a la racine
        root = await Folder.find_one(
            Folder.organization_id == PydanticObjectId(org_id),
            Folder.is_root == True,  # noqa: E712
        )
        if root:
            restore_folder_id = root.id
        else:
            raise ValidationError(
                "Impossible de restaurer : le dossier d'origine et "
                "le dossier racine n'existent plus"
            )

    now = datetime.now(UTC)
    # Gestion des doublons dans le dossier de restauration
    resolved = await _resolve_duplicate_name(file_doc.name, restore_folder_id)

    await file_doc.set({
        "deleted_at": None,
        "original_folder_id": None,
        "folder_id": restore_folder_id,
        "name": resolved,
        "updated_at": now,
    })

    await log_action(
        user.id, "FILE_RESTORE",
        f"File '{file_doc.name}' restored from trash",
    )
    return file_doc


# ─── US-FILE-09 : Suppression en masse ──────────────────────────

async def bulk_delete(
    user: User, org_id: str,
    file_ids: list[str], folder_ids: list[str],
) -> dict:
    """
    Suppression en masse de fichiers ET dossiers.
    Les elements sans droit ou proteges (racine, corbeille) sont ignores.
    """
    from app.features.folders.service import soft_delete_folder

    files_deleted = 0
    folders_deleted = 0
    skipped = 0
    details: list[dict] = []

    for fid in file_ids:
        try:
            await soft_delete_file(user, org_id, fid)
            files_deleted += 1
        except (ForbiddenError, NotFoundError, ValidationError) as e:
            skipped += 1
            details.append({
                "type": "file",
                "id": fid,
                "reason": str(e.detail) if hasattr(e, "detail") else str(e),
            })

    for fid in folder_ids:
        try:
            await soft_delete_folder(str(user.id), fid)
            folders_deleted += 1
        except (ForbiddenError, NotFoundError, ValidationError) as e:
            skipped += 1
            details.append({
                "type": "folder",
                "id": fid,
                "reason": str(e.detail) if hasattr(e, "detail") else str(e),
            })
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Unexpected error deleting folder %s", fid,
            )
            skipped += 1
            details.append({
                "type": "folder",
                "id": fid,
                "reason": "Erreur interne",
            })

    return {
        "files_deleted": files_deleted,
        "folders_deleted": folders_deleted,
        "skipped": skipped,
        "details": details,
    }


# ─── US-FILE-10/11 : Consultation & telechargement ──────────────

async def get_file_detail(
    user: User, org_id: str, file_id: str,
    version_id: str | None = None,
) -> tuple[File, str, str]:
    """
    Retourne (file_doc, base64_content, mime_type) pour affichage frontend.
    Si version_id est fourni, lit cette version spécifique.
    Sinon, lit la version courante.
    """
    import base64

    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id))

    if version_id:
        version = await FileVersion.get(PydanticObjectId(version_id))
        if not version or str(version.file_id) != str(file_doc.id):
            raise NotFoundError("Version non trouvée")
    else:
        version = await FileVersion.find_one(
            FileVersion.file_id == file_doc.id,
            FileVersion.version_number == file_doc.current_version,
        )
        if not version:
            raise NotFoundError("Version courante introuvable")

    full = _full_path(version.storage_path)
    if not full.exists():
        raise NotFoundError("Fichier physique introuvable")

    content = full.read_bytes()
    b64 = base64.b64encode(content).decode("utf-8")

    return file_doc, b64, version.mime_type or file_doc.mime_type


async def get_download_path(
    user: User, org_id: str, file_id: str,
    version_id: str | None = None,
) -> tuple[Path, str]:
    """
    Retourne (chemin_physique, nom_fichier) pour le telechargement.
    Si version_id est fourni, telecharge cette version specifique.
    Sinon, telecharge la version courante.
    """
    file_doc = await _get_file(file_id, org_id)
    await check_folder_access(str(user.id), str(file_doc.folder_id))

    if version_id:
        version = await FileVersion.get(PydanticObjectId(version_id))
        if not version or str(version.file_id) != str(file_doc.id):
            raise NotFoundError("Version non trouvee")
    else:
        version = await FileVersion.find_one(
            FileVersion.file_id == file_doc.id,
            FileVersion.version_number == file_doc.current_version,
        )
        if not version:
            raise NotFoundError("Version courante introuvable")

    full = _full_path(version.storage_path)
    if not full.exists():
        raise NotFoundError("Fichier physique introuvable")

    await log_action(
        user.id, "FILE_DOWNLOAD",
        f"File '{file_doc.name}' v{version.version_number} downloaded",
    )
    return full, file_doc.name


# ─── Listing ─────────────────────────────────────────────────────

async def list_folder_files(
    user: User, org_id: str, folder_id: str,
    page: int = 1, page_size: int = 50,
    search: str | None = None,
    mime_type: str | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
):
    """
    Liste les fichiers d'un dossier (non supprimes), pagine et filtrable.
    Filtres : search (nom), mime_type (ex: "application/pdf", "image/*").
    Tri : name, size, created_at, updated_at (asc ou desc).
    """
    import re

    from app.core.pagination import paginate

    await check_folder_access(str(user.id), folder_id)

    filters: dict = {
        "folder_id": PydanticObjectId(folder_id),
        "organization_id": PydanticObjectId(org_id),
        "deleted_at": None,
    }

    if search:
        filters["name"] = {"$regex": re.escape(search), "$options": "i"}

    if mime_type:
        if mime_type.endswith("/*"):
            # Filtre par categorie (ex: "image/*" → "image/")
            prefix = mime_type.replace("/*", "/")
            filters["mime_type"] = {"$regex": f"^{re.escape(prefix)}"}
        else:
            filters["mime_type"] = mime_type

    valid_sorts = {"name", "size", "created_at", "updated_at"}
    field = sort_by if sort_by in valid_sorts else "name"
    sort_field = f"-{field}" if sort_order == "desc" else f"+{field}"

    query = File.find(filters)
    return await paginate(query, page, page_size, sort_field=sort_field)


async def list_trash_files(org_id: str) -> list[File]:
    """Liste les fichiers dans la corbeille de l'organisation."""
    return await File.find(
        File.organization_id == PydanticObjectId(org_id),
        File.deleted_at != None,  # noqa: E711
    ).sort("-deleted_at").to_list()


# ─── Purge & nettoyage ───────────────────────────────────────────

async def purge_file(user: User, org_id: str, file_id: str) -> None:
    """Supprime définitivement un fichier en corbeille (irréversible)."""
    file_doc = await File.get(PydanticObjectId(file_id))
    if not file_doc:
        raise NotFoundError("Fichier non trouvé")
    if str(file_doc.organization_id) != org_id:
        raise NotFoundError("Fichier non trouvé dans cette organisation")
    if file_doc.deleted_at is None:
        raise ValidationError("Ce fichier n'est pas en corbeille")
    await _permanently_delete_file(file_doc)


async def purge_expired_file_trash() -> int:
    """Suppression definitive des fichiers dont la retention de 30j est expiree."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)

    expired = await File.find(
        File.deleted_at != None,  # noqa: E711
        File.deleted_at <= cutoff,
    ).to_list()

    count = 0
    for f in expired:
        await _permanently_delete_file(f)
        count += 1

    return count


async def _permanently_delete_file(file_doc: File) -> None:
    """Supprime definitivement un fichier : versions + fichiers physiques."""
    versions = await FileVersion.find(
        FileVersion.file_id == file_doc.id,
    ).to_list()

    for v in versions:
        full = _full_path(v.storage_path)
        if full.exists():
            full.unlink()
        await v.delete()

    # Supprimer le dossier de stockage s'il est vide
    storage_dir = _full_path(
        f"files/{file_doc.organization_id}/{file_doc.id}"
    )
    if storage_dir.exists() and not any(storage_dir.iterdir()):
        shutil.rmtree(storage_dir, ignore_errors=True)

    await file_doc.delete()


# ─── Helper interne ──────────────────────────────────────────────

async def _get_file(file_id: str, org_id: str) -> File:
    """Recupere un fichier et verifie qu'il appartient a l'organisation."""
    file_doc = await File.get(PydanticObjectId(file_id))
    if not file_doc:
        raise NotFoundError("Fichier non trouve")
    if str(file_doc.organization_id) != org_id:
        raise NotFoundError("Fichier non trouve dans cette organisation")
    if file_doc.deleted_at:
        raise NotFoundError("Ce fichier est dans la corbeille")
    return file_doc
