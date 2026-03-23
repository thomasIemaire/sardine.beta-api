"""
Service de recherche globale.
Recherche par nom/description sur les fichiers, dossiers, agents et flows
d'une organisation.
"""

import re

from beanie import PydanticObjectId

from app.core.membership import check_org_membership
from app.features.agents.models import Agent
from app.features.auth.models import User
from app.features.files.models import File
from app.features.flows.models import Flow
from app.features.folders.models import Folder


async def search(
    user: User, org_id: str, query: str,
    types: list[str] | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    """
    Recherche globale par regex sur le nom et la description.
    types: filtrer par type ("file", "folder", "agent", "flow"). None = tous.
    Retourne {"items": [...], "total": int, "page": int, ...}.
    """
    await check_org_membership(user, org_id)

    if not query or len(query.strip()) < 2:
        return _empty_result(page, page_size)

    oid = PydanticObjectId(org_id)
    escaped = re.escape(query)
    regex_filter = {"$regex": escaped, "$options": "i"}
    search_types = types or ["file", "folder", "agent", "flow"]

    results: list[dict] = []

    if "folder" in search_types:
        folders = await Folder.find(
            Folder.organization_id == oid,
            Folder.deleted_at == None,  # noqa: E711
            {"name": regex_filter},
        ).to_list()
        for f in folders:
            results.append({
                "type": "folder",
                "id": str(f.id),
                "name": f.name,
                "description": None,
                "parent_id": str(f.parent_id) if f.parent_id else None,
                "created_at": f.created_at,
            })

    if "file" in search_types:
        files = await File.find(
            File.organization_id == oid,
            File.deleted_at == None,  # noqa: E711
            {"name": regex_filter},
        ).to_list()
        for f in files:
            results.append({
                "type": "file",
                "id": str(f.id),
                "name": f.name,
                "description": None,
                "parent_id": str(f.folder_id),
                "mime_type": f.mime_type,
                "created_at": f.created_at,
            })

    if "agent" in search_types:
        agents = await Agent.find(
            Agent.organization_id == oid,
            {"$or": [
                {"name": regex_filter},
                {"description": regex_filter},
            ]},
        ).to_list()
        for a in agents:
            results.append({
                "type": "agent",
                "id": str(a.id),
                "name": a.name,
                "description": a.description,
                "parent_id": None,
                "created_at": a.created_at,
            })

    if "flow" in search_types:
        flows = await Flow.find(
            Flow.organization_id == oid,
            {"$or": [
                {"name": regex_filter},
                {"description": regex_filter},
            ]},
        ).to_list()
        for f in flows:
            results.append({
                "type": "flow",
                "id": str(f.id),
                "name": f.name,
                "description": f.description,
                "parent_id": None,
                "created_at": f.created_at,
            })

    # Tri par pertinence (nom exact > debut > contient) puis par date
    query_lower = query.lower()
    results.sort(key=lambda r: (
        0 if r["name"].lower() == query_lower
        else 1 if r["name"].lower().startswith(query_lower)
        else 2,
        -(r["created_at"].timestamp() if r["created_at"] else 0),
    ))

    # Pagination manuelle sur les resultats agreges
    total = len(results)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = results[start:end]

    from math import ceil
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(total / page_size) if total > 0 else 1,
    }


def _empty_result(page: int, page_size: int) -> dict:
    return {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "total_pages": 1,
    }
