"""
Routes agents, versioning, partage et fork.
Toutes les routes nécessitent une authentification
et l'appartenance à l'organisation.
"""

from fastapi import APIRouter, Query

from app.features.agents.schemas import (
    ActiveVersionUpdate,
    AgentCreate,
    AgentRead,
    AgentShareCreate,
    AgentShareRead,
    AgentUpdate,
    AgentVersionCreate,
    AgentVersionRead,
)
from app.features.agents.service import (
    create_agent,
    create_version,
    delete_agent,
    fork_agent,
    get_agent,
    get_shared_agent,
    get_version,
    get_version_history,
    list_agent_shares,
    list_agents,
    list_shared_agents,
    list_versions,
    share_agent,
    switch_active_version,
    unshare_agent,
    update_agent,
)
from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse

router = APIRouter(prefix="/organizations/{org_id}/agents", tags=["Agents"])


# ─── CRUD Agent ──────────────────────────────────────────────────

@router.post("/", response_model=AgentRead, status_code=201)
async def create(org_id: str, payload: AgentCreate, current_user: CurrentUser):
    """Créer un agent avec son schéma initial (1ère version)."""
    agent, version = await create_agent(
        current_user, org_id, payload.name, payload.schema_data,
        description=payload.description,
    )
    return AgentRead.from_agent(agent, active_schema=version.schema_data)


@router.get("/")
async def list_all(
    org_id: str, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Liste des agents de l'organisation (pagine)."""
    result = await list_agents(current_user, org_id, page, page_size)
    return {
        "items": [AgentRead.from_agent(a) for a in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get("/shared", response_model=list[AgentRead])
async def list_shared(org_id: str, current_user: CurrentUser):
    """Liste des agents partagés avec mon organisation (lecture seule)."""
    agents = await list_shared_agents(current_user, org_id)
    return [AgentRead.from_agent(a) for a in agents]


@router.get("/shared/{agent_id}", response_model=AgentRead)
async def get_shared(org_id: str, agent_id: str, current_user: CurrentUser):
    """Détail d'un agent partagé avec mon organisation (lecture seule)."""
    agent, active_schema = await get_shared_agent(
        current_user, org_id, agent_id,
    )
    return AgentRead.from_agent(agent, active_schema=active_schema)


@router.get("/{agent_id}", response_model=AgentRead)
async def get_one(org_id: str, agent_id: str, current_user: CurrentUser):
    """Détail d'un agent avec le schéma de la version active."""
    agent, active_schema = await get_agent(current_user, org_id, agent_id)
    return AgentRead.from_agent(agent, active_schema=active_schema)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update(
    org_id: str, agent_id: str, payload: AgentUpdate, current_user: CurrentUser,
):
    """Modifier le nom et/ou la description d'un agent."""
    agent = await update_agent(
        current_user, org_id, agent_id,
        name=payload.name, description=payload.description,
    )
    return AgentRead.from_agent(agent)


@router.delete("/{agent_id}", response_model=MessageResponse)
async def delete(org_id: str, agent_id: str, current_user: CurrentUser):
    """Supprimer un agent et toutes ses versions."""
    await delete_agent(current_user, org_id, agent_id)
    return MessageResponse(message="Agent supprimé")


# ─── Versioning ──────────────────────────────────────────────────

@router.post(
    "/{agent_id}/versions", response_model=AgentVersionRead, status_code=201,
)
async def create_ver(
    org_id: str, agent_id: str,
    payload: AgentVersionCreate, current_user: CurrentUser,
):
    """
    Créer une nouvelle version.
    Si parent_version_id est omis, branche depuis la version active.
    Auto-checkout sur la nouvelle version.
    """
    version = await create_version(
        current_user, org_id, agent_id,
        payload.schema_data, payload.parent_version_id,
    )
    return AgentVersionRead.from_version(version)


@router.get("/{agent_id}/versions", response_model=list[AgentVersionRead])
async def list_ver(
    org_id: str, agent_id: str, current_user: CurrentUser,
):
    """Liste toutes les versions d'un agent (arbre complet)."""
    versions = await list_versions(current_user, org_id, agent_id)
    return [AgentVersionRead.from_version(v) for v in versions]


@router.get(
    "/{agent_id}/versions/{version_id}", response_model=AgentVersionRead,
)
async def get_ver(
    org_id: str, agent_id: str, version_id: str, current_user: CurrentUser,
):
    """Détail d'une version spécifique."""
    version = await get_version(current_user, org_id, agent_id, version_id)
    return AgentVersionRead.from_version(version)


@router.patch("/{agent_id}/active-version", response_model=AgentRead)
async def switch_version(
    org_id: str, agent_id: str,
    payload: ActiveVersionUpdate, current_user: CurrentUser,
):
    """Changer la version active (checkout)."""
    agent = await switch_active_version(
        current_user, org_id, agent_id, payload.version_id,
    )
    return AgentRead.from_agent(agent)


@router.get(
    "/{agent_id}/versions/{version_id}/history",
    response_model=list[AgentVersionRead],
)
async def version_history(
    org_id: str, agent_id: str, version_id: str, current_user: CurrentUser,
):
    """
    Historique d'une version : chaîne des ancêtres
    jusqu'à la version initiale (git log).
    """
    history = await get_version_history(
        current_user, org_id, agent_id, version_id,
    )
    return [AgentVersionRead.from_version(v) for v in history]


# ─── Partage ─────────────────────────────────────────────────────

@router.post(
    "/{agent_id}/shares", response_model=list[AgentShareRead], status_code=201,
)
async def share(
    org_id: str, agent_id: str,
    payload: AgentShareCreate, current_user: CurrentUser,
):
    """Partager un agent en lecture seule avec une ou plusieurs organisations."""
    shares = await share_agent(
        current_user, org_id, agent_id, payload.target_org_ids,
    )
    return [AgentShareRead.from_share(s) for s in shares]


@router.get("/{agent_id}/shares", response_model=list[AgentShareRead])
async def list_shares(
    org_id: str, agent_id: str, current_user: CurrentUser,
):
    """Liste des organisations avec lesquelles l'agent est partagé."""
    shares = await list_agent_shares(current_user, org_id, agent_id)
    return [AgentShareRead.from_share(s) for s in shares]


@router.delete("/{agent_id}/shares/{target_org_id}", response_model=MessageResponse)
async def remove_share(
    org_id: str, agent_id: str, target_org_id: str, current_user: CurrentUser,
):
    """Retirer le partage d'un agent avec une organisation."""
    await unshare_agent(current_user, org_id, agent_id, target_org_id)
    return MessageResponse(message="Partage retiré")


# ─── Fork ────────────────────────────────────────────────────────

@router.post("/fork/{agent_id}", response_model=AgentRead, status_code=201)
async def fork(org_id: str, agent_id: str, current_user: CurrentUser):
    """
    Fork un agent partagé dans mon organisation.
    Crée une copie liée à l'original par les versions.
    """
    agent, version = await fork_agent(current_user, org_id, agent_id)
    return AgentRead.from_agent(agent, active_schema=version.schema_data)
