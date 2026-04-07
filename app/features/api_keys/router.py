"""
Endpoints de gestion des clés API d'une organisation.
"""

from fastapi import APIRouter, Query, status

from app.core.users_lookup import get_user_names_map
from app.features.api_keys.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.features.api_keys.service import (
    create_api_key,
    delete_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.features.auth.dependencies import CurrentUser

router = APIRouter(
    prefix="/organizations/{org_id}/api-keys",
    tags=["API Keys"],
)


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreated)
async def create_key(org_id: str, payload: ApiKeyCreate, current_user: CurrentUser):
    """Crée une nouvelle clé API. Le token n'est retourné qu'une seule fois."""
    api_key, token = await create_api_key(current_user, org_id, payload)
    names = await get_user_names_map([api_key.created_by])
    return ApiKeyCreated.from_model(
        api_key, token=token,
        creator_name=names.get(str(api_key.created_by)),
    )


@router.get("/")
async def list_keys(
    org_id: str,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Liste paginée des clés API de l'organisation."""
    result = await list_api_keys(current_user, org_id, page=page, page_size=page_size)
    names = await get_user_names_map([k.created_by for k in result.items])
    return {
        "items": [
            ApiKeyRead.from_model(k, creator_name=names.get(str(k.created_by)))
            for k in result.items
        ],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.patch("/{key_id}/revoke", response_model=ApiKeyRead)
async def revoke_key(org_id: str, key_id: str, current_user: CurrentUser):
    """Révoque une clé API active. Action irréversible."""
    api_key = await revoke_api_key(current_user, org_id, key_id)
    names = await get_user_names_map([api_key.created_by])
    return ApiKeyRead.from_model(
        api_key, creator_name=names.get(str(api_key.created_by)),
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(org_id: str, key_id: str, current_user: CurrentUser):
    """Supprime définitivement une clé API."""
    await delete_api_key(current_user, org_id, key_id)
