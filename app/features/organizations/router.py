"""
Routes organisations.
Toutes les routes nécessitent une authentification.
"""

from fastapi import APIRouter

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.organizations.schemas import (
    OrganizationCreate,
    OrganizationInvite,
    OrganizationRead,
    OrganizationUpdate,
)
from app.features.organizations.service import (
    create_organization,
    invite_user_to_organization,
    list_user_organizations,
    update_organization,
)

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.post("/", response_model=OrganizationRead, status_code=201)
async def create_org(payload: OrganizationCreate, current_user: CurrentUser):
    """
    Créer une organisation collaborative.
    Crée automatiquement le dossier racine, la corbeille et l'équipe racine.
    """
    org = await create_organization(current_user, payload)
    return OrganizationRead.from_org(org)


@router.patch("/{org_id}", response_model=OrganizationRead)
async def update_org(org_id: str, payload: OrganizationUpdate, current_user: CurrentUser):
    """Modifier une organisation (propriétaire uniquement)."""
    org = await update_organization(current_user, org_id, payload)
    return OrganizationRead.from_org(org)


@router.get("/", response_model=list[OrganizationRead])
async def list_orgs(current_user: CurrentUser):
    """
    Liste de toutes les organisations accessibles.
    L'organisation privée est toujours en premier.
    """
    orgs = await list_user_organizations(current_user)
    return [OrganizationRead.from_org(o) for o in orgs]


# ─── Invitations ─────────────────────────────────────────────────

@router.post("/{org_id}/invite", response_model=MessageResponse, status_code=201)
async def invite_to_org(
    org_id: str, payload: OrganizationInvite, current_user: CurrentUser,
):
    """
    Inviter un utilisateur à rejoindre l'organisation.
    L'utilisateur reçoit une notification action (Accepter / Refuser).
    """
    await invite_user_to_organization(current_user, org_id, payload.user_id)
    return MessageResponse(message="Invitation envoyée")
