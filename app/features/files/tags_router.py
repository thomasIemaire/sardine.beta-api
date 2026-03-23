"""
Routes pour le systeme de tags (fichiers, agents, flows).
"""

from fastapi import APIRouter, Query

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.files.tags import (
    TagCreate,
    TagRead,
    add_tag,
    list_resource_tags,
    remove_tag,
    search_by_tag,
)

router = APIRouter(
    prefix="/organizations/{org_id}/tags",
    tags=["Tags"],
)


@router.post(
    "/{resource_type}/{resource_id}",
    response_model=TagRead,
    status_code=201,
)
async def create_tag(
    org_id: str, resource_type: str, resource_id: str,
    payload: TagCreate, current_user: CurrentUser,
):
    """Ajouter un tag a une ressource (file, agent, flow)."""
    tag = await add_tag(
        current_user, org_id,
        resource_type, resource_id,
        payload.name, payload.color,
    )
    return TagRead.from_tag(tag)


@router.get(
    "/{resource_type}/{resource_id}",
    response_model=list[TagRead],
)
async def get_tags(
    org_id: str, resource_type: str, resource_id: str,
    current_user: CurrentUser,
):
    """Liste des tags d'une ressource."""
    tags = await list_resource_tags(
        current_user, org_id, resource_type, resource_id,
    )
    return [TagRead.from_tag(t) for t in tags]


@router.delete("/{tag_id}", response_model=MessageResponse)
async def delete_tag(
    org_id: str, tag_id: str, current_user: CurrentUser,
):
    """Supprimer un tag."""
    await remove_tag(current_user, org_id, tag_id)
    return MessageResponse(message="Tag supprime")


@router.get("/", response_model=list[TagRead])
async def search_tags(
    org_id: str, current_user: CurrentUser,
    name: str = Query(..., min_length=1),
    resource_type: str | None = Query(None),
):
    """Rechercher toutes les ressources ayant un tag donne."""
    tags = await search_by_tag(
        current_user, org_id, name, resource_type,
    )
    return [TagRead.from_tag(t) for t in tags]
