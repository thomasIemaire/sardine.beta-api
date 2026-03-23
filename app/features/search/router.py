"""
Route de recherche globale.
"""

from fastapi import APIRouter, Query

from app.features.auth.dependencies import CurrentUser
from app.features.search.service import search

router = APIRouter(
    prefix="/organizations/{org_id}/search",
    tags=["Search"],
)


@router.get("/")
async def global_search(
    org_id: str,
    current_user: CurrentUser,
    q: str = Query(..., min_length=2),
    types: str | None = Query(None, description="file,folder,agent,flow"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    Recherche globale dans l'organisation.
    Cherche par nom et description sur fichiers, dossiers, agents, flows.
    Filtrer par type : ?types=file,agent
    """
    type_list = types.split(",") if types else None
    return await search(
        current_user, org_id, q,
        types=type_list, page=page, page_size=page_size,
    )
