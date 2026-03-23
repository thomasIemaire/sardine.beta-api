"""
Routes équipes, membres et sous-équipes.
Toutes les routes nécessitent une authentification.
"""

from fastapi import APIRouter

from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.teams.schemas import (
    MemberAdd,
    MemberRead,
    MemberRoleUpdate,
    MemberStatusUpdate,
    SubTeamCreate,
    TeamCreate,
    TeamRead,
    TeamTreeNode,
    TeamUpdate,
)
from app.features.teams.service import (
    add_member,
    create_sub_team,
    create_team,
    delete_team,
    get_team_tree,
    list_team_members,
    list_user_teams,
    update_member_role,
    update_member_status,
    update_team,
)

router = APIRouter(prefix="/organizations/{org_id}/teams", tags=["Teams"])


# ─── Équipes ──────────────────────────────────────────────────────

@router.post("/", response_model=TeamRead, status_code=201)
async def create(org_id: str, payload: TeamCreate, current_user: CurrentUser):
    """Créer une équipe dans l'organisation."""
    team = await create_team(current_user, org_id, payload.name)
    return TeamRead.from_team(team)


@router.get("/", response_model=list[dict])
async def list_teams(org_id: str, current_user: CurrentUser):
    """Liste des équipes auxquelles l'utilisateur appartient."""
    teams = await list_user_teams(current_user, org_id)
    return [
        {
            "team": TeamRead.from_team(t["team"]),
            "role": t["role"],
        }
        for t in teams
    ]


@router.patch("/{team_id}", response_model=TeamRead)
async def update(team_id: str, payload: TeamUpdate, current_user: CurrentUser):
    """Renommer une équipe (hors équipe racine)."""
    team = await update_team(current_user, team_id, payload.name)
    return TeamRead.from_team(team)


@router.delete("/{team_id}", response_model=MessageResponse)
async def delete(team_id: str, current_user: CurrentUser):
    """Supprimer une équipe (hors équipe racine)."""
    await delete_team(current_user, team_id)
    return MessageResponse(message="Équipe supprimée")


# ─── Membres ──────────────────────────────────────────────────────

@router.post("/{team_id}/members", response_model=MessageResponse, status_code=201)
async def add_team_member(
    team_id: str, payload: MemberAdd, current_user: CurrentUser,
):
    """Ajouter un membre à l'équipe (rôle Membre par défaut)."""
    await add_member(current_user, team_id, payload.user_id)
    return MessageResponse(message="Membre ajouté")


@router.patch("/{team_id}/members/{user_id}/role", response_model=MessageResponse)
async def change_member_role(
    team_id: str, user_id: str,
    payload: MemberRoleUpdate, current_user: CurrentUser,
):
    """Modifier le rôle d'un membre (Propriétaire ↔ Membre)."""
    await update_member_role(current_user, team_id, user_id, payload.role)
    return MessageResponse(message="Rôle modifié")


@router.patch("/{team_id}/members/{user_id}/status", response_model=MessageResponse)
async def change_member_status(
    team_id: str, user_id: str,
    payload: MemberStatusUpdate, current_user: CurrentUser,
):
    """Activer/désactiver un membre."""
    await update_member_status(current_user, team_id, user_id, payload.status)
    return MessageResponse(message="Statut modifié")


@router.get("/{team_id}/members", response_model=list[MemberRead])
async def get_members(team_id: str, current_user: CurrentUser):
    """
    Liste des membres (directs + propriétaires hérités).
    Les propriétaires hérités sont marqués avec inherited=True.
    """
    members = await list_team_members(team_id)
    return [
        MemberRead.from_member(m["member"], m["user"], m["inherited"])
        for m in members
    ]


# ─── Sous-équipes & hiérarchie ────────────────────────────────────

@router.post("/sub-teams", response_model=TeamRead, status_code=201)
async def create_sub(org_id: str, payload: SubTeamCreate, current_user: CurrentUser):
    """
    Créer une sous-équipe rattachée à un ou plusieurs parents.
    Relation N-N supportée.
    """
    team = await create_sub_team(
        current_user, org_id, payload.name, payload.parent_team_ids,
    )
    return TeamRead.from_team(team)


@router.get("/tree", response_model=list[TeamTreeNode])
async def team_tree(org_id: str, current_user: CurrentUser):
    """
    Arborescence complète des équipes de l'organisation.
    Les équipes dont l'utilisateur est membre sont mises en évidence.
    """
    return await get_team_tree(org_id, current_user)
