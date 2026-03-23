"""
Helper de pagination reutilisable pour tous les endpoints de listing.
"""

from math import ceil
from typing import Any

from pydantic import BaseModel


class PaginatedResponse(BaseModel):
    """Reponse paginee generique."""

    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


async def paginate(
    query,
    page: int = 1,
    page_size: int = 20,
    sort_field: str = "-created_at",
) -> PaginatedResponse:
    """
    Pagine une requete Beanie.
    `query` est un FindMany (ex: Model.find({...})).
    Retourne un PaginatedResponse.
    """
    total = await query.count()
    total_pages = ceil(total / page_size) if total > 0 else 1
    skip = (page - 1) * page_size

    items = await query.sort(sort_field).skip(skip).limit(page_size).to_list()

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
