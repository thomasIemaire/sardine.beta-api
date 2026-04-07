"""
Routes flows, versioning, partage et fork.
Toutes les routes nécessitent une authentification
et l'appartenance à l'organisation.
"""

from fastapi import APIRouter, Query

from app.core.users_lookup import get_user_names_map
from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import MessageResponse
from app.features.flows.schemas import (
    ActiveVersionUpdate,
    FlowCreate,
    FlowRead,
    FlowShareCreate,
    FlowShareRead,
    FlowUpdate,
    FlowVersionCreate,
    FlowVersionRead,
)
from app.features.flows.service import (
    create_flow,
    create_version,
    delete_flow,
    fork_flow,
    get_flow,
    get_shared_flow,
    get_version,
    get_version_history,
    list_flow_shares,
    list_flows,
    list_shared_flows,
    list_versions,
    share_flow,
    switch_active_version,
    unshare_flow,
    update_flow,
)

router = APIRouter(prefix="/organizations/{org_id}/flows", tags=["Flows"])


# ─── CRUD Flow ───────────────────────────────────────────────────

@router.post("/", response_model=FlowRead, status_code=201)
async def create(org_id: str, payload: FlowCreate, current_user: CurrentUser):
    """Créer un flow avec ses données initiales (1ère version)."""
    flow, version = await create_flow(
        current_user, org_id, payload.name, payload.flow_data,
        description=payload.description,
    )
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, active_data=version.flow_data,
        creator_name=names.get(str(flow.created_by)),
    )


@router.get("/")
async def list_all(
    org_id: str, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str | None = Query(None),
    creator: str | None = Query(None),
    origin: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    status: str | None = Query(None),
):
    """Liste des flows de l'organisation (pagine, filtrable, triable)."""
    result = await list_flows(
        current_user, org_id, page, page_size,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
        status=status,
    )
    names = await get_user_names_map([f.created_by for f in result.items])
    return {
        "items": [
            FlowRead.from_flow(f, creator_name=names.get(str(f.created_by)))
            for f in result.items
        ],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get("/shared", response_model=list[FlowRead])
async def list_shared(
    org_id: str, current_user: CurrentUser,
    search: str | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str | None = Query(None),
    creator: str | None = Query(None),
    origin: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    status: str | None = Query(None),
):
    """Liste des flows partagés avec mon organisation (lecture seule)."""
    flows = await list_shared_flows(
        current_user, org_id,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
        status=status,
    )
    names = await get_user_names_map([f.created_by for f in flows])
    return [
        FlowRead.from_flow(f, creator_name=names.get(str(f.created_by)))
        for f in flows
    ]


@router.get("/shared/{flow_id}", response_model=FlowRead)
async def get_shared(org_id: str, flow_id: str, current_user: CurrentUser):
    """Détail d'un flow partagé avec mon organisation (lecture seule)."""
    flow, active_data = await get_shared_flow(
        current_user, org_id, flow_id,
    )
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, active_data=active_data,
        creator_name=names.get(str(flow.created_by)),
    )


@router.get("/{flow_id}", response_model=FlowRead)
async def get_one(org_id: str, flow_id: str, current_user: CurrentUser):
    """Détail d'un flow avec les données de la version active."""
    flow, active_data = await get_flow(current_user, org_id, flow_id)
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, active_data=active_data,
        creator_name=names.get(str(flow.created_by)),
    )


@router.patch("/{flow_id}", response_model=FlowRead)
async def update(
    org_id: str, flow_id: str, payload: FlowUpdate, current_user: CurrentUser,
):
    """Modifier le nom, la description et/ou le statut d'un flow."""
    flow = await update_flow(
        current_user, org_id, flow_id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
    )
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, creator_name=names.get(str(flow.created_by)),
    )


@router.delete("/{flow_id}", response_model=MessageResponse)
async def delete(org_id: str, flow_id: str, current_user: CurrentUser):
    """Supprimer un flow et toutes ses versions."""
    await delete_flow(current_user, org_id, flow_id)
    return MessageResponse(message="Flow supprimé")


# ─── Versioning ──────────────────────────────────────────────────

@router.post(
    "/{flow_id}/versions", response_model=FlowVersionRead, status_code=201,
)
async def create_ver(
    org_id: str, flow_id: str,
    payload: FlowVersionCreate, current_user: CurrentUser,
):
    """
    Créer une nouvelle version.
    Si parent_version_id est omis, branche depuis la version active.
    Auto-checkout sur la nouvelle version.
    """
    version = await create_version(
        current_user, org_id, flow_id,
        payload.flow_data, payload.parent_version_id,
    )
    names = await get_user_names_map([version.created_by])
    return FlowVersionRead.from_version(
        version, creator_name=names.get(str(version.created_by)),
    )


@router.get("/{flow_id}/versions", response_model=list[FlowVersionRead])
async def list_ver(
    org_id: str, flow_id: str, current_user: CurrentUser,
):
    """Liste toutes les versions d'un flow (arbre complet)."""
    versions = await list_versions(current_user, org_id, flow_id)
    names = await get_user_names_map([v.created_by for v in versions])
    return [
        FlowVersionRead.from_version(v, creator_name=names.get(str(v.created_by)))
        for v in versions
    ]


@router.get(
    "/{flow_id}/versions/{version_id}", response_model=FlowVersionRead,
)
async def get_ver(
    org_id: str, flow_id: str, version_id: str, current_user: CurrentUser,
):
    """Détail d'une version spécifique."""
    version = await get_version(current_user, org_id, flow_id, version_id)
    names = await get_user_names_map([version.created_by])
    return FlowVersionRead.from_version(
        version, creator_name=names.get(str(version.created_by)),
    )


@router.patch("/{flow_id}/active-version", response_model=FlowRead)
async def switch_version(
    org_id: str, flow_id: str,
    payload: ActiveVersionUpdate, current_user: CurrentUser,
):
    """Changer la version active (checkout)."""
    flow = await switch_active_version(
        current_user, org_id, flow_id, payload.version_id,
    )
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, creator_name=names.get(str(flow.created_by)),
    )


@router.get(
    "/{flow_id}/versions/{version_id}/history",
    response_model=list[FlowVersionRead],
)
async def version_history(
    org_id: str, flow_id: str, version_id: str, current_user: CurrentUser,
):
    """
    Historique d'une version : chaîne des ancêtres
    jusqu'à la version initiale (git log).
    """
    history = await get_version_history(
        current_user, org_id, flow_id, version_id,
    )
    names = await get_user_names_map([v.created_by for v in history])
    return [
        FlowVersionRead.from_version(v, creator_name=names.get(str(v.created_by)))
        for v in history
    ]


# ─── Partage ─────────────────────────────────────────────────────

@router.post(
    "/{flow_id}/shares", response_model=list[FlowShareRead], status_code=201,
)
async def share(
    org_id: str, flow_id: str,
    payload: FlowShareCreate, current_user: CurrentUser,
):
    """Partager un flow en lecture seule avec une ou plusieurs organisations."""
    shares = await share_flow(
        current_user, org_id, flow_id, payload.target_org_ids,
    )
    return [FlowShareRead.from_share(s) for s in shares]


@router.get("/{flow_id}/shares", response_model=list[FlowShareRead])
async def list_shares(
    org_id: str, flow_id: str, current_user: CurrentUser,
):
    """Liste des organisations avec lesquelles le flow est partagé."""
    shares = await list_flow_shares(current_user, org_id, flow_id)
    return [FlowShareRead.from_share(s) for s in shares]


@router.delete("/{flow_id}/shares/{target_org_id}", response_model=MessageResponse)
async def remove_share(
    org_id: str, flow_id: str, target_org_id: str, current_user: CurrentUser,
):
    """Retirer le partage d'un flow avec une organisation."""
    await unshare_flow(current_user, org_id, flow_id, target_org_id)
    return MessageResponse(message="Partage retiré")


# ─── Fork ────────────────────────────────────────────────────────

@router.post("/fork/{flow_id}", response_model=FlowRead, status_code=201)
async def fork(org_id: str, flow_id: str, current_user: CurrentUser):
    """
    Fork un flow partagé dans mon organisation.
    Crée une copie liée à l'original par les versions.
    """
    flow, version = await fork_flow(current_user, org_id, flow_id)
    names = await get_user_names_map([flow.created_by])
    return FlowRead.from_flow(
        flow, active_data=version.flow_data,
        creator_name=names.get(str(flow.created_by)),
    )
