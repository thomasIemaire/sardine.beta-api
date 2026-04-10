"""
Routes agents, versioning, partage et fork.
Toutes les routes nécessitent une authentification
et l'appartenance à l'organisation.
"""

import asyncio

from beanie import PydanticObjectId
from fastapi import APIRouter, Query, UploadFile

from app.features.agents.schemas import (
    ActiveVersionUpdate,
    AgentCreate,
    AgentRead,
    AgentShareCreate,
    AgentShareRead,
    AgentUpdate,
    AgentVersionCreate,
    AgentVersionRead,
    FieldFeedbackRead,
    FieldFeedbackSubmit,
)
from app.features.agents.service import (
    create_agent,
    create_version,
    delete_agent,
    export_agent,
    export_shared_agent,
    fork_agent,
    get_agent,
    get_agent_stats,
    get_file_feedbacks,
    get_shared_agent,
    get_used_agent_ids,
    get_version,
    get_version_history,
    import_agent,
    list_agent_shares,
    list_agents,
    list_shared_agents,
    list_trashed_agents,
    list_versions,
    purge_agent,
    restore_agent,
    share_agent,
    submit_field_feedback,
    switch_active_version,
    unshare_agent,
    update_agent,
)
from app.core.users_lookup import get_user_names_map
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
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, active_schema=version.schema_data,
        creator_name=names.get(str(agent.created_by)),
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
):
    """Liste des agents de l'organisation (pagine, filtrable, triable)."""
    result = await list_agents(
        current_user, org_id, page, page_size,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
    )
    names, used_ids = await asyncio.gather(
        get_user_names_map([a.created_by for a in result.items]),
        get_used_agent_ids(org_id),
    )
    return {
        "items": [
            AgentRead.from_agent(
                a,
                creator_name=names.get(str(a.created_by)),
                used_in_flows=str(a.id) in used_ids,
            )
            for a in result.items
        ],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get("/shared", response_model=list[AgentRead])
async def list_shared(
    org_id: str, current_user: CurrentUser,
    search: str | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str | None = Query(None),
    creator: str | None = Query(None),
    origin: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
):
    """Liste des agents partagés avec mon organisation (lecture seule)."""
    items = await list_shared_agents(
        current_user, org_id,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
    )
    names = await get_user_names_map([a.created_by for a, _ in items])
    return [
        AgentRead.from_agent(
            a, active_schema=s,
            creator_name=names.get(str(a.created_by)),
        )
        for a, s in items
    ]


@router.get("/shared/{agent_id}", response_model=AgentRead)
async def get_shared(org_id: str, agent_id: str, current_user: CurrentUser):
    """Détail d'un agent partagé avec mon organisation (lecture seule)."""
    agent, active_schema = await get_shared_agent(
        current_user, org_id, agent_id,
    )
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, active_schema=active_schema,
        creator_name=names.get(str(agent.created_by)),
    )


# ─── Corbeille ───────────────────────────────────────────────────
# Déclarée AVANT /{agent_id} pour éviter le conflit de route

@router.get("/trash", response_model=list[AgentRead])
async def list_trash(org_id: str, current_user: CurrentUser):
    """Liste les agents en corbeille de l'organisation."""
    agents = await list_trashed_agents(current_user, org_id)
    names = await get_user_names_map([a.created_by for a in agents])
    return [
        AgentRead.from_agent(a, creator_name=names.get(str(a.created_by)))
        for a in agents
    ]


@router.get("/{agent_id}", response_model=AgentRead)
async def get_one(org_id: str, agent_id: str, current_user: CurrentUser):
    """Détail d'un agent avec le schéma de la version active."""
    agent, active_schema = await get_agent(current_user, org_id, agent_id)
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, active_schema=active_schema,
        creator_name=names.get(str(agent.created_by)),
    )


@router.patch("/{agent_id}", response_model=AgentRead)
async def update(
    org_id: str, agent_id: str, payload: AgentUpdate, current_user: CurrentUser,
):
    """Modifier le nom et/ou la description d'un agent."""
    agent = await update_agent(
        current_user, org_id, agent_id,
        name=payload.name, description=payload.description,
    )
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, creator_name=names.get(str(agent.created_by)),
    )


@router.delete("/{agent_id}", response_model=MessageResponse)
async def delete(org_id: str, agent_id: str, current_user: CurrentUser):
    """Déplacer un agent dans la corbeille (suppression douce)."""
    await delete_agent(current_user, org_id, agent_id)
    return MessageResponse(message="Agent déplacé dans la corbeille")


@router.post("/{agent_id}/restore", response_model=AgentRead)
async def restore(org_id: str, agent_id: str, current_user: CurrentUser):
    """Restaurer un agent depuis la corbeille."""
    agent = await restore_agent(current_user, org_id, agent_id)
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(agent, creator_name=names.get(str(agent.created_by)))


@router.delete("/{agent_id}/purge", response_model=MessageResponse)
async def purge(org_id: str, agent_id: str, current_user: CurrentUser):
    """Supprimer définitivement un agent en corbeille (irréversible)."""
    await purge_agent(current_user, org_id, agent_id)
    return MessageResponse(message="Agent supprimé définitivement")


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
    names = await get_user_names_map([version.created_by])
    return AgentVersionRead.from_version(
        version, creator_name=names.get(str(version.created_by)),
    )


@router.get("/{agent_id}/versions", response_model=list[AgentVersionRead])
async def list_ver(
    org_id: str, agent_id: str, current_user: CurrentUser,
):
    """Liste toutes les versions d'un agent (arbre complet)."""
    versions = await list_versions(current_user, org_id, agent_id)
    names = await get_user_names_map([v.created_by for v in versions])
    return [
        AgentVersionRead.from_version(v, creator_name=names.get(str(v.created_by)))
        for v in versions
    ]


@router.get(
    "/{agent_id}/versions/{version_id}", response_model=AgentVersionRead,
)
async def get_ver(
    org_id: str, agent_id: str, version_id: str, current_user: CurrentUser,
):
    """Détail d'une version spécifique."""
    version = await get_version(current_user, org_id, agent_id, version_id)
    names = await get_user_names_map([version.created_by])
    return AgentVersionRead.from_version(
        version, creator_name=names.get(str(version.created_by)),
    )


@router.patch("/{agent_id}/active-version", response_model=AgentRead)
async def switch_version(
    org_id: str, agent_id: str,
    payload: ActiveVersionUpdate, current_user: CurrentUser,
):
    """Changer la version active (checkout)."""
    agent = await switch_active_version(
        current_user, org_id, agent_id, payload.version_id,
    )
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, creator_name=names.get(str(agent.created_by)),
    )


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
    names = await get_user_names_map([v.created_by for v in history])
    return [
        AgentVersionRead.from_version(v, creator_name=names.get(str(v.created_by)))
        for v in history
    ]


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
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, active_schema=version.schema_data,
        creator_name=names.get(str(agent.created_by)),
    )


# ─── Export/Import ───────────────────────────────────────────────

@router.get("/{agent_id}/export")
async def export(org_id: str, agent_id: str, current_user: CurrentUser):
    """
    Télécharger un agent au format JSON.
    Retourne le fichier JSON avec les métadonnées et le schéma actif.
    """
    from fastapi.responses import Response
    import json

    data = await export_agent(current_user, org_id, agent_id)
    json_content = json.dumps(data, indent=2, ensure_ascii=False)

    return Response(
        content=json_content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{data["name"]}.json"'
        },
    )


@router.get("/shared/{agent_id}/export")
async def export_shared(org_id: str, agent_id: str, current_user: CurrentUser):
    """
    Télécharger un agent partagé au format JSON.
    Même logique que l'export normal, mais pour les agents partagés.
    """
    from fastapi.responses import Response
    import json

    data = await export_shared_agent(current_user, org_id, agent_id)
    json_content = json.dumps(data, indent=2, ensure_ascii=False)

    return Response(
        content=json_content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{data["name"]}.json"'
        },
    )


@router.post("/import", response_model=AgentRead, status_code=201)
async def import_agent_route(
    org_id: str, file: UploadFile, current_user: CurrentUser,
):
    """
    Importer un agent depuis un fichier JSON uploadé.
    Le fichier doit contenir le format exporté (nom, description, schema_data).
    """
    import json

    if not file.filename.endswith(".json"):
        raise ValidationError("Le fichier doit être au format JSON")

    content = await file.read()
    try:
        data = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError:
        raise ValidationError("Contenu JSON invalide")

    agent, version = await import_agent(current_user, org_id, data)
    names = await get_user_names_map([agent.created_by])
    return AgentRead.from_agent(
        agent, active_schema=version.schema_data,
        creator_name=names.get(str(agent.created_by)),
    )


# ─── Feedback & statistiques ─────────────────────────────────────

@router.post("/{agent_id}/feedback", response_model=list[FieldFeedbackRead], status_code=201)
async def submit_feedback(
    org_id: str, agent_id: str,
    payload: FieldFeedbackSubmit, current_user: CurrentUser,
):
    """
    Soumettre des feedbacks sur les champs extraits par un agent pour un fichier.
    Si un feedback existe déjà pour ce champ+fichier, il est remplacé.
    """
    feedbacks = await submit_field_feedback(
        current_user, org_id, agent_id,
        payload.file_id,
        [fb.model_dump() for fb in payload.feedbacks],
    )
    return [FieldFeedbackRead.from_feedback(fb) for fb in feedbacks]


@router.get("/{agent_id}/stats")
async def agent_stats(org_id: str, agent_id: str, current_user: CurrentUser):
    """Statistiques de précision d'un agent (global + par champ)."""
    return await get_agent_stats(current_user, org_id, agent_id)


@router.get("/{agent_id}/feedback", response_model=list[FieldFeedbackRead])
async def list_feedback(org_id: str, agent_id: str, current_user: CurrentUser):
    """Liste tous les feedbacks soumis pour un agent."""
    from app.features.agents.models import AgentFieldFeedback
    feedbacks = await AgentFieldFeedback.find({
        "agent_id": PydanticObjectId(agent_id),
        "organization_id": PydanticObjectId(org_id),
    }).to_list()
    return [FieldFeedbackRead.from_feedback(fb) for fb in feedbacks]
