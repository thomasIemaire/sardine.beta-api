"""
Systeme de tags sur les fichiers, agents et flows.
Un tag est un label libre attache a une ressource dans le contexte d'une organisation.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.core.exceptions import ConflictError, NotFoundError
from app.core.membership import check_org_membership
from app.features.auth.models import User

# ─── Modele ──────────────────────────────────────────────────────

class Tag(Document):
    """
    Tag attache a une ressource.
    Cle logique unique : (organization_id, resource_type, resource_id, name).
    """

    organization_id: Indexed(PydanticObjectId)
    resource_type: Indexed(str)  # "file", "agent", "flow"
    resource_id: Indexed(PydanticObjectId)
    name: str  # Label du tag (ex: "urgent", "v2", "archive")
    color: str = "#6B7280"  # Couleur hex pour l'affichage

    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "tags"


# ─── Schemas ─────────────────────────────────────────────────────

class TagCreate(BaseModel):
    name: str
    color: str = "#6B7280"


class TagRead(BaseModel):
    id: str
    name: str
    color: str
    resource_type: str
    resource_id: str
    created_by: str
    created_at: datetime

    @classmethod
    def from_tag(cls, tag) -> "TagRead":
        return cls(
            id=str(tag.id),
            name=tag.name,
            color=tag.color,
            resource_type=tag.resource_type,
            resource_id=str(tag.resource_id),
            created_by=str(tag.created_by),
            created_at=tag.created_at,
        )


# ─── Service ─────────────────────────────────────────────────────

async def add_tag(
    user: User, org_id: str,
    resource_type: str, resource_id: str,
    name: str, color: str = "#6B7280",
) -> Tag:
    """Ajouter un tag a une ressource."""
    await check_org_membership(user, org_id)

    if resource_type not in ("file", "agent", "flow"):
        from app.core.exceptions import ValidationError
        raise ValidationError("resource_type doit etre 'file', 'agent' ou 'flow'")

    oid = PydanticObjectId(org_id)
    rid = PydanticObjectId(resource_id)

    # Verifier que la ressource existe dans cette organisation
    await _validate_resource_exists(resource_type, rid, oid)

    # Unicite du tag sur la meme ressource
    existing = await Tag.find_one(
        Tag.organization_id == oid,
        Tag.resource_type == resource_type,
        Tag.resource_id == rid,
        Tag.name == name,
    )
    if existing:
        raise ConflictError(f"Le tag '{name}' existe deja sur cette ressource")

    tag = Tag(
        organization_id=oid,
        resource_type=resource_type,
        resource_id=rid,
        name=name,
        color=color,
        created_by=user.id,
    )
    await tag.insert()
    return tag


async def remove_tag(
    user: User, org_id: str, tag_id: str,
) -> None:
    """Supprimer un tag."""
    await check_org_membership(user, org_id)

    tag = await Tag.get(PydanticObjectId(tag_id))
    if not tag:
        raise NotFoundError("Tag non trouve")
    if str(tag.organization_id) != org_id:
        raise NotFoundError("Tag non trouve dans cette organisation")
    await tag.delete()


async def list_resource_tags(
    user: User, org_id: str,
    resource_type: str, resource_id: str,
) -> list[Tag]:
    """Liste les tags d'une ressource."""
    await check_org_membership(user, org_id)

    return await Tag.find(
        Tag.organization_id == PydanticObjectId(org_id),
        Tag.resource_type == resource_type,
        Tag.resource_id == PydanticObjectId(resource_id),
    ).sort("name").to_list()


async def search_by_tag(
    user: User, org_id: str,
    tag_name: str,
    resource_type: str | None = None,
) -> list[Tag]:
    """Recherche toutes les ressources ayant un tag donne."""
    await check_org_membership(user, org_id)

    filters = {
        "organization_id": PydanticObjectId(org_id),
        "name": tag_name,
    }
    if resource_type:
        filters["resource_type"] = resource_type

    return await Tag.find(filters).to_list()


async def _validate_resource_exists(
    resource_type: str, resource_id: PydanticObjectId,
    org_id: PydanticObjectId,
) -> None:
    """Verifie que la ressource existe dans l'organisation."""
    if resource_type == "file":
        from app.features.files.models import File
        r = await File.get(resource_id)
        if not r or r.organization_id != org_id:
            raise NotFoundError("Fichier non trouve dans cette organisation")
    elif resource_type == "agent":
        from app.features.agents.models import Agent
        r = await Agent.get(resource_id)
        if not r or r.organization_id != org_id:
            raise NotFoundError("Agent non trouve dans cette organisation")
    elif resource_type == "flow":
        from app.features.flows.models import Flow
        r = await Flow.get(resource_id)
        if not r or r.organization_id != org_id:
            raise NotFoundError("Flow non trouve dans cette organisation")
