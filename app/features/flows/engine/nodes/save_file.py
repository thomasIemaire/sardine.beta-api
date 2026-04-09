"""
Nœud save_file — sauvegarde le fichier en cours de traitement dans les dossiers de l'org.

Config attendu :
  {
    "path": "/factures/2024/"   // chemin relatif depuis la racine org
  }

  - Le chemin est découpé en segments (ex: ["factures", "2024"]).
  - Les dossiers manquants sont créés automatiquement.
  - Si "path" est vide ou absent, le fichier est rangé à la racine.

Lit   : context.data["fileBase64"], context.data["fileName"], context.data["fileMimeType"]
Écrit : context.data["savedFile"] = { "fileId", "folderId", "path", "name" }

1 port de sortie : 0 (succès ou erreur propagée).
"""

import base64
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from beanie import PydanticObjectId

from app.features.files.models import File, FileVersion
from app.features.folders.models import Folder
from app.features.folders.schemas import FolderCreate
from app.features.folders.service import create_folder, get_root_folder

from ..context import ExecutionContext, NodeResult

STORAGE_DIR = Path("storage/files")


def _parse_path(raw: str) -> list[str]:
    """Découpe '/factures/2024/' en ['factures', '2024']. Filtre les segments vides."""
    return [s.strip() for s in raw.strip("/").split("/") if s.strip()]


async def _resolve_or_create_folder(org_id: str, segments: list[str]) -> Folder:
    """
    Parcourt l'arborescence segment par segment depuis la racine.
    Crée les dossiers manquants au passage.
    """
    parent = await get_root_folder(org_id)

    for segment in segments:
        existing = await Folder.find_one(
            Folder.organization_id == PydanticObjectId(org_id),
            Folder.parent_id == parent.id,
            Folder.name == segment,
            Folder.deleted_at == None,  # noqa: E711
        )
        if existing:
            parent = existing
        else:
            payload = FolderCreate(name=segment, parent_id=str(parent.id))
            parent = await create_folder(org_id, payload)

    return parent


def _storage_path(org_id: str, file_id: str, version: int, ext: str) -> str:
    return f"files/{org_id}/{file_id}/v{version}{ext}"


def _full_path(relative: str) -> Path:
    return Path("storage") / relative


async def _resolve_duplicate_name(name: str, folder_id: PydanticObjectId) -> str:
    """Ajoute un suffixe numérique si un fichier du même nom existe déjà."""
    base = Path(name)
    stem = base.stem
    suffix = base.suffix
    candidate = name
    counter = 1

    while True:
        existing = await File.find_one(
            File.name == candidate,
            File.folder_id == folder_id,
            File.deleted_at == None,  # noqa: E711
        )
        if existing is None:
            return candidate
        candidate = f"{stem}({counter}){suffix}"
        counter += 1
        if counter > 100:
            return f"{stem}_flow{suffix}"


async def execute_save_file(
    node: dict, context: ExecutionContext, engine,
) -> NodeResult:
    config = node.get("config", {})
    raw_path = config.get("path", "")

    # Récupérer les données du fichier depuis le contexte
    file_b64 = context.data.get("fileBase64")
    if not file_b64 or not isinstance(file_b64, str):
        return NodeResult(error="SaveFile: champ 'fileBase64' manquant dans context.data")

    file_name = context.data.get("fileName") or "fichier"
    file_mime = context.data.get("fileMimeType") or ""
    org_id = context.metadata.get("org_id")
    triggered_by = context.metadata.get("triggered_by")

    if not org_id:
        return NodeResult(error="SaveFile: org_id manquant dans le contexte d'exécution")

    # Décoder le base64 en bytes
    try:
        # Supprimer l'éventuel préfixe data URL (ex: "data:application/pdf;base64,...")
        if "," in file_b64:
            file_b64 = file_b64.split(",", 1)[1]
        content = base64.b64decode(file_b64)
    except Exception as exc:
        return NodeResult(error=f"SaveFile: impossible de décoder le fichier base64 — {exc}")

    # Résoudre ou créer le dossier cible
    try:
        segments = _parse_path(raw_path)
        target_folder = await _resolve_or_create_folder(org_id, segments)
    except Exception as exc:
        return NodeResult(error=f"SaveFile: erreur lors de la résolution du chemin '{raw_path}' — {exc}")

    # Déduire l'extension et le MIME
    ext = Path(file_name).suffix.lower()
    if not file_mime:
        file_mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    # Gérer les doublons de nom
    resolved_name = await _resolve_duplicate_name(file_name, target_folder.id)

    # Créer le document File en base
    uploaded_by_oid = PydanticObjectId(triggered_by) if triggered_by else PydanticObjectId()
    file_doc = File(
        name=resolved_name,
        folder_id=target_folder.id,
        organization_id=PydanticObjectId(org_id),
        current_version=1,
        mime_type=file_mime,
        size=len(content),
        uploaded_by=uploaded_by_oid,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await file_doc.insert()

    # Écrire le fichier physique
    rel_path = _storage_path(org_id, str(file_doc.id), 1, ext)
    full = _full_path(rel_path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)

    # Créer la première version
    version = FileVersion(
        file_id=file_doc.id,
        version_number=1,
        storage_path=rel_path,
        original_name=file_name,
        mime_type=file_mime,
        size=len(content),
        uploaded_by=uploaded_by_oid,
        created_at=datetime.now(UTC),
    )
    await version.insert()

    # Construire le chemin lisible pour le résultat
    display_path = "/" + "/".join(segments) + "/" if segments else "/"

    context.data["savedFile"] = {
        "fileId": str(file_doc.id),
        "folderId": str(target_folder.id),
        "path": display_path,
        "name": resolved_name,
        "size": len(content),
        "mimeType": file_mime,
    }

    return NodeResult(
        output_port=0,
        metadata={
            "file_id": str(file_doc.id),
            "folder_id": str(target_folder.id),
            "path": display_path,
            "name": resolved_name,
        },
    )
