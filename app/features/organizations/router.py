"""
Routes organisations.
Toutes les routes nécessitent une authentification.
"""

from fastapi import APIRouter

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.organizations.schemas import (
    BulkInviteRequest,
    BulkInviteResult,
    OrganizationCreate,
    OrganizationInvite,
    OrganizationRead,
    OrganizationUpdate,
)
from app.features.organizations.service import (
    bulk_invite_members,
    create_organization,
    invite_user_to_organization,
    list_child_organizations,
    list_distributed_organizations,
    list_organization_members,
    list_owned_organizations,
    list_user_organizations,
    update_organization,
)
from app.features.teams.schemas import MemberRead

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


@router.get("/owned", response_model=list[OrganizationRead])
async def list_owned_orgs(current_user: CurrentUser):
    """Liste des organisations dont l'utilisateur est propriétaire (admin)."""
    orgs = await list_owned_organizations(current_user)
    return [OrganizationRead.from_org(o) for o in orgs]


@router.get("/{org_id}/children", response_model=list[OrganizationRead])
async def list_children(org_id: str, current_user: CurrentUser):
    """Liste des organisations enfants (clientes). Owner requis."""
    orgs = await list_child_organizations(current_user, org_id)
    return [OrganizationRead.from_org(o) for o in orgs]


@router.get("/{org_id}/distributed", response_model=list[OrganizationRead])
async def list_distributed(org_id: str, current_user: CurrentUser):
    """Liste des organisations distribuées par cette organisation. Owner requis."""
    orgs = await list_distributed_organizations(current_user, org_id)
    return [OrganizationRead.from_org(o) for o in orgs]


@router.get("/{org_id}/members", response_model=list[MemberRead])
async def list_org_members(org_id: str, current_user: CurrentUser):
    """Liste des membres de l'organisation (via l'équipe racine)."""
    members = await list_organization_members(current_user, org_id)
    return [MemberRead.from_member(m["member"], m["user"]) for m in members]


@router.post(
    "/{org_id}/members/invite-bulk",
    response_model=list[BulkInviteResult],
    status_code=201,
)
async def invite_bulk(
    org_id: str, payload: BulkInviteRequest, current_user: CurrentUser,
):
    """
    Invitation en masse : crée les comptes si nécessaire
    et ajoute les utilisateurs à l'organisation.
    """
    results = await bulk_invite_members(current_user, org_id, payload.members)
    return results


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
