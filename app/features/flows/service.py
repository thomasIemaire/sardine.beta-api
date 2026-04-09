"""
Service flows — CRUD, versioning et gestion des branches.

Le versioning fonctionne comme un arbre (DAG) :
chaque version pointe vers son parent via parent_version_id.
Le flow maintient un pointeur active_version_id (HEAD)
vers la version courante.
"""

from datetime import UTC, datetime

from beanie import PydanticObjectId

from app.core.enums import FlowStatus
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.membership import check_org_membership
from app.features.agents.models import Agent
from app.features.auth.models import User
from app.features.flows.models import Flow, FlowShare, FlowVersion


async def _get_flow_for_org(flow_id: str, org_id: str, include_deleted: bool = False) -> Flow:
    """Récupère un flow et vérifie qu'il appartient à l'organisation."""
    flow = await Flow.get(PydanticObjectId(flow_id))
    if not flow:
        raise NotFoundError("Flow non trouvé")
    if str(flow.organization_id) != org_id:
        raise NotFoundError("Flow non trouvé dans cette organisation")
    if not include_deleted and flow.deleted_at is not None:
        raise NotFoundError("Flow non trouvé")
    return flow


# ─── CRUD Flow ───────────────────────────────────────────────────

async def create_flow(
    user: User, org_id: str, name: str, flow_data: dict,
    description: str = "",
) -> tuple[Flow, FlowVersion]:
    """
    Crée un flow avec sa version initiale.
    Retourne le flow et la première version.
    """
    await check_org_membership(user, org_id)

    # 1. Créer le flow (sans version active pour l'instant)
    flow = Flow(
        name=name,
        description=description,
        organization_id=PydanticObjectId(org_id),
        created_by=user.id,
    )
    await flow.insert()

    # 2. Créer la version initiale
    version = FlowVersion(
        flow_id=flow.id,
        flow_data=flow_data,
        parent_version_id=None,
        created_by=user.id,
    )
    await version.insert()

    # 3. Positionner le HEAD
    await flow.set({"active_version_id": version.id})

    return flow, version


async def list_flows(
    user: User, org_id: str, page: int = 1, page_size: int = 20,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    creator: str | None = None,
    origin: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    status: str | None = None,
):
    """Liste tous les flows d'une organisation (pagine, filtrable, triable)."""
    from app.core.filters import build_filters, resolve_sort
    from app.core.pagination import paginate

    await check_org_membership(user, org_id)

    filters = build_filters(
        search=search, creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
        status=status,
        valid_statuses={FlowStatus.ACTIVE, FlowStatus.ERROR, FlowStatus.PENDING},
    )
    sort_field = resolve_sort(
        sort_by, sort_dir,
        allowed_fields={"name", "created_at", "status"},
    )

    query = Flow.find(
        Flow.organization_id == PydanticObjectId(org_id),
        {"deleted_at": None},
        filters,
    )
    return await paginate(query, page, page_size, sort_field=sort_field)


async def get_flow(
    user: User, org_id: str, flow_id: str,
) -> tuple[Flow, dict | None]:
    """
    Détail d'un flow avec les données de sa version active.
    Retourne (flow, flow_data_de_la_version_active).
    """
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    active_data = None
    if flow.active_version_id:
        version = await FlowVersion.get(flow.active_version_id)
        if version:
            active_data = version.flow_data

    return flow, active_data


async def update_flow(
    user: User, org_id: str, flow_id: str,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> Flow:
    """Modifier le nom, la description et/ou le statut d'un flow."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    update_data: dict = {}
    if name is not None:
        update_data["name"] = name
    if description is not None:
        update_data["description"] = description
    if status is not None:
        valid = {FlowStatus.ACTIVE, FlowStatus.ERROR, FlowStatus.PENDING}
        if status not in valid:
            raise ValidationError(
                f"Statut invalide. Valeurs possibles : {', '.join(valid)}"
            )
        update_data["status"] = status

    if update_data:
        update_data["updated_at"] = datetime.now(UTC)
        await flow.set(update_data)

    return flow


async def delete_flow(user: User, org_id: str, flow_id: str) -> None:
    """Déplace un flow dans la corbeille (suppression douce)."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)
    await flow.set({"deleted_at": datetime.now(UTC), "updated_at": datetime.now(UTC)})


async def list_trashed_flows(user: User, org_id: str) -> list[Flow]:
    """Liste les flows en corbeille de l'organisation."""
    await check_org_membership(user, org_id)
    return await Flow.find(
        Flow.organization_id == PydanticObjectId(org_id),
        {"deleted_at": {"$ne": None}},
    ).sort("-deleted_at").to_list()


async def restore_flow(user: User, org_id: str, flow_id: str) -> Flow:
    """Restaure un flow depuis la corbeille."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id, include_deleted=True)
    if flow.deleted_at is None:
        raise ValidationError("Ce flow n'est pas en corbeille")
    await flow.set({"deleted_at": None, "updated_at": datetime.now(UTC)})
    return flow


async def purge_flow(user: User, org_id: str, flow_id: str) -> None:
    """Supprime définitivement un flow en corbeille et toutes ses versions."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id, include_deleted=True)
    if flow.deleted_at is None:
        raise ValidationError("Ce flow n'est pas en corbeille")
    await FlowVersion.find(FlowVersion.flow_id == flow.id).delete()
    await FlowShare.find(FlowShare.flow_id == flow.id).delete()
    await flow.delete()


async def purge_expired_flow_trash(days: int = 30) -> int:
    """
    Supprime définitivement les flows en corbeille depuis plus de `days` jours.
    Appelé par la tâche de fond périodique.
    """
    from datetime import timedelta
    cutoff = datetime.now(UTC) - timedelta(days=days)
    expired = await Flow.find(
        {"deleted_at": {"$ne": None, "$lt": cutoff}}
    ).to_list()

    count = 0
    for flow in expired:
        await FlowVersion.find(FlowVersion.flow_id == flow.id).delete()
        await FlowShare.find(FlowShare.flow_id == flow.id).delete()
        await flow.delete()
        count += 1
    return count


# ─── Versioning ──────────────────────────────────────────────────

async def create_version(
    user: User,
    org_id: str,
    flow_id: str,
    flow_data: dict,
    parent_version_id: str | None = None,
) -> FlowVersion:
    """
    Crée une nouvelle version du flow.
    Si parent_version_id n'est pas fourni, branche depuis la version active.
    Auto-checkout : la nouvelle version devient la version active.
    """
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    # Résoudre le parent
    if parent_version_id:
        parent = await FlowVersion.get(PydanticObjectId(parent_version_id))
        if not parent or str(parent.flow_id) != str(flow.id):
            raise NotFoundError("Version parente non trouvée pour ce flow")
        resolved_parent_id = parent.id
    elif flow.active_version_id:
        resolved_parent_id = flow.active_version_id
    else:
        raise ValidationError("Aucune version parente disponible")

    # Créer la nouvelle version
    version = FlowVersion(
        flow_id=flow.id,
        flow_data=flow_data,
        parent_version_id=resolved_parent_id,
        created_by=user.id,
    )
    await version.insert()

    # Auto-checkout
    await flow.set({
        "active_version_id": version.id,
        "updated_at": datetime.now(UTC),
    })

    return version


async def list_versions(
    user: User, org_id: str, flow_id: str,
) -> list[FlowVersion]:
    """Liste toutes les versions d'un flow (arbre complet)."""
    await check_org_membership(user, org_id)
    await _get_flow_for_org(flow_id, org_id)

    return await FlowVersion.find(
        FlowVersion.flow_id == PydanticObjectId(flow_id),
    ).sort("+created_at").to_list()


async def get_version(
    user: User, org_id: str, flow_id: str, version_id: str,
) -> FlowVersion:
    """Détail d'une version spécifique."""
    await check_org_membership(user, org_id)
    await _get_flow_for_org(flow_id, org_id)

    version = await FlowVersion.get(PydanticObjectId(version_id))
    if not version or str(version.flow_id) != flow_id:
        raise NotFoundError("Version non trouvée pour ce flow")

    return version


async def switch_active_version(
    user: User, org_id: str, flow_id: str, version_id: str,
) -> Flow:
    """Change la version active du flow (checkout)."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    # Vérifier que la version existe et appartient à ce flow
    version = await FlowVersion.get(PydanticObjectId(version_id))
    if not version or str(version.flow_id) != str(flow.id):
        raise NotFoundError("Version non trouvée pour ce flow")

    await flow.set({
        "active_version_id": version.id,
        "updated_at": datetime.now(UTC),
    })
    return flow


async def get_version_history(
    user: User, org_id: str, flow_id: str, version_id: str,
) -> list[FlowVersion]:
    """
    Historique d'une version : remonte la chaîne des ancêtres
    jusqu'à la version initiale (git log).
    """
    await check_org_membership(user, org_id)
    await _get_flow_for_org(flow_id, org_id)

    version = await FlowVersion.get(PydanticObjectId(version_id))
    if not version or str(version.flow_id) != flow_id:
        raise NotFoundError("Version non trouvée pour ce flow")

    history = [version]
    current = version
    max_depth = 100  # Sécurité anti-boucle infinie

    while current.parent_version_id and len(history) < max_depth:
        parent = await FlowVersion.get(current.parent_version_id)
        if not parent:
            break
        # Stopper a la frontiere du flow (ne pas traverser les forks)
        if str(parent.flow_id) != flow_id:
            break
        history.append(parent)
        current = parent

    return history


# ─── Partage ─────────────────────────────────────────────────────

async def share_flow(
    user: User, org_id: str, flow_id: str, target_org_ids: list[str],
) -> list[FlowShare]:
    """
    Partage un flow en lecture seule avec une ou plusieurs organisations.
    Seul un membre de l'org propriétaire peut partager.
    Les orgs déjà partagées sont ignorées silencieusement.
    """
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    from app.core.membership import get_org_owner_user_ids
    from app.features.notifications.service import create_info_notification
    from app.features.organizations.models import Organization

    # Org source (pour le message)
    source_org = await Organization.get(PydanticObjectId(org_id))
    source_org_name = source_org.name if source_org else "une organisation"

    created: list[FlowShare] = []
    for target_org_id in target_org_ids:
        if org_id == target_org_id:
            continue

        target_org = await Organization.get(PydanticObjectId(target_org_id))
        if not target_org:
            raise NotFoundError(f"Organisation cible {target_org_id} non trouvée")

        existing = await FlowShare.find_one(
            FlowShare.flow_id == flow.id,
            FlowShare.shared_with_org_id == PydanticObjectId(target_org_id),
        )
        if existing:
            continue

        share = FlowShare(
            flow_id=flow.id,
            shared_with_org_id=PydanticObjectId(target_org_id),
            shared_by=user.id,
        )
        await share.insert()
        created.append(share)

        # Notifier tous les propriétaires de l'org cible
        owner_ids = await get_org_owner_user_ids(target_org.id)
        for owner_id in owner_ids:
            await create_info_notification(
                recipient_user_id=owner_id,
                title="Nouveau flow partagé",
                message=(
                    f"L'organisation « {source_org_name} » a partagé "
                    f"le flow « {flow.name} » avec votre organisation."
                ),
                organization_id=str(target_org.id),
            )

    return created


async def unshare_flow(
    user: User, org_id: str, flow_id: str, target_org_id: str,
) -> None:
    """Retire le partage d'un flow avec une organisation."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    share = await FlowShare.find_one(
        FlowShare.flow_id == flow.id,
        FlowShare.shared_with_org_id == PydanticObjectId(target_org_id),
    )
    if not share:
        raise NotFoundError("Partage non trouvé")

    await share.delete()


async def list_flow_shares(
    user: User, org_id: str, flow_id: str,
) -> list[FlowShare]:
    """Liste les organisations avec lesquelles un flow est partagé."""
    await check_org_membership(user, org_id)
    await _get_flow_for_org(flow_id, org_id)

    return await FlowShare.find(
        FlowShare.flow_id == PydanticObjectId(flow_id),
    ).to_list()


async def list_shared_flows(
    user: User, org_id: str,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    creator: str | None = None,
    origin: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    status: str | None = None,
) -> list[Flow]:
    """Liste les flows partagés avec mon organisation (lecture seule)."""
    from app.core.filters import build_filters, resolve_sort

    await check_org_membership(user, org_id)

    shares = await FlowShare.find(
        FlowShare.shared_with_org_id == PydanticObjectId(org_id),
    ).to_list()

    flow_ids = [s.flow_id for s in shares]
    if not flow_ids:
        return []

    filters = build_filters(
        search=search, creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
        status=status,
        valid_statuses={FlowStatus.ACTIVE, FlowStatus.ERROR, FlowStatus.PENDING},
    )
    sort_field = resolve_sort(
        sort_by, sort_dir,
        allowed_fields={"name", "created_at", "status"},
    )

    return await Flow.find(
        {"_id": {"$in": flow_ids}},
        filters,
    ).sort(sort_field).to_list()


async def get_shared_flow(
    user: User, org_id: str, flow_id: str,
) -> tuple[Flow, dict | None]:
    """
    Détail d'un flow partagé avec mon organisation (lecture seule).
    Vérifie que le partage existe.
    """
    await check_org_membership(user, org_id)

    flow = await Flow.get(PydanticObjectId(flow_id))
    if not flow:
        raise NotFoundError("Flow non trouvé")

    share = await FlowShare.find_one(
        FlowShare.flow_id == flow.id,
        FlowShare.shared_with_org_id == PydanticObjectId(org_id),
    )
    if not share:
        raise ForbiddenError("Ce flow n'est pas partagé avec votre organisation")

    active_data = None
    if flow.active_version_id:
        version = await FlowVersion.get(flow.active_version_id)
        if version:
            active_data = version.flow_data

    return flow, active_data


# ─── Fork ────────────────────────────────────────────────────────

async def fork_flow(
    user: User, org_id: str, flow_id: str,
) -> tuple[Flow, FlowVersion]:
    """
    Fork un flow partagé dans mon organisation.
    Crée une copie du flow avec la version active de l'original
    comme première version. Le parent_version_id de la copie pointe
    vers la version d'origine (traçabilité).
    """
    await check_org_membership(user, org_id)

    source_flow = await Flow.get(PydanticObjectId(flow_id))
    if not source_flow:
        raise NotFoundError("Flow source non trouvé")

    # Vérifier que le partage existe (sauf si c'est notre propre org)
    if str(source_flow.organization_id) != org_id:
        share = await FlowShare.find_one(
            FlowShare.flow_id == source_flow.id,
            FlowShare.shared_with_org_id == PydanticObjectId(org_id),
        )
        if not share:
            raise ForbiddenError(
                "Ce flow n'est pas partagé avec votre organisation"
            )

    if not source_flow.active_version_id:
        raise ValidationError("Le flow source n'a pas de version active")
    source_version = await FlowVersion.get(source_flow.active_version_id)
    if not source_version:
        raise ValidationError("Version active du flow source introuvable")

    forked_flow = Flow(
        name=source_flow.name,
        description=source_flow.description,
        organization_id=PydanticObjectId(org_id),
        forked_from_id=source_flow.id,
        forked_from_version_id=source_version.id,
        created_by=user.id,
    )
    await forked_flow.insert()

    forked_version = FlowVersion(
        flow_id=forked_flow.id,
        flow_data=source_version.flow_data,
        parent_version_id=source_version.id,
        created_by=user.id,
    )
    await forked_version.insert()

    await forked_flow.set({"active_version_id": forked_version.id})

    return forked_flow, forked_version


# ─── Export/Import ───────────────────────────────────────────────

def _extract_subflow_ids(flow_data: dict) -> set[str]:
    """Extrait les flowId référencés dans les noeuds de type 'flow'."""
    ids = set()
    for node in flow_data.get("nodes", []):
        if node.get("type") == "flow":
            fid = node.get("config", {}).get("flowId", "").strip()
            if fid:
                ids.add(fid)
    return ids


async def _export_subflows_recursive(
    org_id: str,
    flow_data: dict,
    visited: set[str],
) -> list[dict]:
    """
    Exporte récursivement tous les subflows référencés dans flow_data.
    visited contient les flow_ids déjà traités — protège contre les cycles.
    Retourne une liste ordonnée : parent avant ses enfants.
    """
    result = []
    for sfid in _extract_subflow_ids(flow_data):
        if sfid in visited:
            continue
        visited.add(sfid)

        try:
            sf = await Flow.get(PydanticObjectId(sfid))
            if not sf or str(sf.organization_id) != org_id:
                continue
            if not sf.active_version_id:
                continue
            sf_version = await FlowVersion.get(sf.active_version_id)
            if not sf_version:
                continue

            # Descendre d'abord dans les enfants du subflow
            nested = await _export_subflows_recursive(org_id, sf_version.flow_data, visited)

            result.append({
                "id": sfid,
                "name": sf.name,
                "description": sf.description,
                "flow_data": sf_version.flow_data,
            })
            result.extend(nested)
        except Exception:
            pass

    return result


def _update_flow_id_refs(data: dict | list, flow_id_map: dict) -> None:
    """Remplace récursivement les flowId dans flow_data selon le mapping old→new."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "flowId" and isinstance(value, str) and value in flow_id_map:
                data[key] = flow_id_map[value]
            else:
                _update_flow_id_refs(value, flow_id_map)
    elif isinstance(data, list):
        for item in data:
            _update_flow_id_refs(item, flow_id_map)


def _extract_agent_ids(data: dict | list | str | int | float | bool | None) -> set[str]:
    """
    Extrait récursivement tous les IDs d'agents référencés dans flow_data.
    Recherche les clés "agent_id", "agents", ou toute valeur string qui ressemble à un ObjectId MongoDB (24 caractères hex).
    """
    import re

    agent_ids = set()

    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("agent_id", "agents"):
                if isinstance(value, str):
                    agent_ids.add(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            agent_ids.add(item)
            elif isinstance(value, str) and re.match(r'^[a-f0-9]{24}$', value):
                # Ajout automatique des strings qui ressemblent à des ObjectIds
                agent_ids.add(value)
            else:
                agent_ids.update(_extract_agent_ids(value))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str) and re.match(r'^[a-f0-9]{24}$', item):
                agent_ids.add(item)
            else:
                agent_ids.update(_extract_agent_ids(item))

    return agent_ids


async def export_flow(user: User, org_id: str, flow_id: str) -> dict:
    """
    Exporte un flow au format JSON pour téléchargement.
    Inclut les métadonnées, flow_data, et les agents référencés.
    """
    from app.features.agents.service import export_agent

    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    if not flow.active_version_id:
        raise ValidationError("Le flow n'a pas de version active")

    version = await FlowVersion.get(flow.active_version_id)
    if not version:
        raise ValidationError("Version active introuvable")

    # Extraire les agents référencés
    agent_ids = _extract_agent_ids(version.flow_data)
    agents = []
    for agent_id in agent_ids:
        try:
            agent_data = await export_agent(user, org_id, agent_id)
            agents.append(agent_data)
        except (NotFoundError, ValidationError):
            pass

    # Exporter les subflows récursivement (visited initialisé avec le flow courant
    # pour couper toute boucle : un flow ne peut pas se référencer lui-même)
    subflows = await _export_subflows_recursive(
        org_id, version.flow_data, visited={flow_id}
    )

    return {
        "name": flow.name,
        "description": flow.description,
        "status": flow.status,
        "flow_data": version.flow_data,
        "agents": agents,
        "subflows": subflows,
        "exported_at": datetime.now(UTC).isoformat(),
        "version": "1.0",
    }


async def export_shared_flow(user: User, org_id: str, flow_id: str) -> dict:
    """
    Exporte un flow partagé au format JSON.
    Même logique que export_flow, mais pour les flows partagés.
    """
    from app.features.agents.service import export_shared_agent

    flow, flow_data = await get_shared_flow(user, org_id, flow_id)

    if not flow_data:
        raise ValidationError("Le flow partagé n'a pas de données actives")

    # Extraire les agents référencés
    agent_ids = _extract_agent_ids(flow_data)
    agents = []
    for agent_id in agent_ids:
        try:
            agent_data = await export_shared_agent(user, org_id, agent_id)
            agents.append(agent_data)
        except (NotFoundError, ValidationError):
            pass

    # Exporter les subflows récursivement
    subflows = await _export_subflows_recursive(
        org_id, flow_data, visited={str(flow.id)}
    )

    return {
        "name": flow.name,
        "description": flow.description,
        "status": flow.status,
        "flow_data": flow_data,
        "agents": agents,
        "subflows": subflows,
        "exported_at": datetime.now(UTC).isoformat(),
        "version": "1.0",
    }


async def import_flow(
    user: User, org_id: str, data: dict,
) -> tuple[Flow, FlowVersion]:
    """
    Importe un flow depuis un JSON exporté.
    Crée les agents manquants, puis le flow avec sa version initiale.
    """
    from app.features.agents.service import import_agent

    await check_org_membership(user, org_id)

    # Validation basique
    if not isinstance(data, dict):
        raise ValidationError("Format JSON invalide")
    if "name" not in data or not isinstance(data["name"], str):
        raise ValidationError("Nom du flow manquant ou invalide")
    if "flow_data" not in data or not isinstance(data["flow_data"], dict):
        raise ValidationError("Données du flow manquantes ou invalides")

    name = data["name"]
    description = data.get("description", "")
    flow_data = data["flow_data"]
    agents_data = data.get("agents", [])
    subflows_data = data.get("subflows", [])

    # ── 1. Importer les agents manquants ─────────────────────────
    agent_id_map: dict[str, str] = {}  # old_id -> new_id
    for agent_data in agents_data:
        if not isinstance(agent_data, dict) or "name" not in agent_data:
            continue
        try:
            existing_agent = await Agent.find_one(
                Agent.name == agent_data["name"],
                Agent.organization_id == PydanticObjectId(org_id),
            )
            if existing_agent:
                agent_id_map[agent_data.get("id", agent_data["name"])] = str(existing_agent.id)
            else:
                agent, _ = await import_agent(user, org_id, agent_data)
                agent_id_map[agent_data.get("id", agent_data["name"])] = str(agent.id)
        except Exception as exc:
            print(f"[IMPORT] ✗ erreur agent {agent_data.get('name')!r} : {exc!r}")

    def _update_agent_refs(d):
        if isinstance(d, dict):
            for key, value in d.items():
                if key == "agent_id" and isinstance(value, str) and value in agent_id_map:
                    d[key] = agent_id_map[value]
                elif key == "agents" and isinstance(value, list):
                    d[key] = [agent_id_map.get(item, item) if isinstance(item, str) else item for item in value]
                else:
                    _update_agent_refs(value)
        elif isinstance(d, list):
            for item in d:
                _update_agent_refs(item)

    # ── 2. Importer les subflows en ordre inverse (feuilles d'abord) ──
    # L'export produit [sous-flow1, sous-flow2, sous-flow3, apro].
    # reversed() → [apro, sous-flow3, sous-flow2, sous-flow1]
    # Chaque feuille est créée avant que son parent soit traité,
    # donc flow_id_map est complet au moment de la mise à jour des refs.
    flow_id_map: dict[str, str] = {}     # old_id → new_id
    created_subflows: list[tuple[Flow, FlowVersion]] = []

    print(f"[IMPORT] subflows à traiter : {len(subflows_data)}")
    for sf_data in reversed(subflows_data):
        if not isinstance(sf_data, dict) or "name" not in sf_data:
            print(f"[IMPORT] ⚠ subflow ignoré (format invalide) : {sf_data!r}")
            continue
        old_id = sf_data.get("id", "")
        sf_name = sf_data["name"]
        sf_description = sf_data.get("description", "")
        sf_flow_data = sf_data.get("flow_data", {})

        print(f"[IMPORT] → traitement subflow : {sf_name!r} (old_id={old_id!r})")
        try:
            existing_sf = await Flow.find_one(
                Flow.name == sf_name,
                Flow.organization_id == PydanticObjectId(org_id),
            )
            if existing_sf:
                # Déjà présent : on mappe l'ancien ID vers l'existant
                print(f"[IMPORT] ✓ subflow existant : {sf_name!r} → {existing_sf.id}")
                if old_id:
                    flow_id_map[old_id] = str(existing_sf.id)
            else:
                # Résoudre les refs agents + subflows déjà créés, puis créer
                _update_agent_refs(sf_flow_data)
                _update_flow_id_refs(sf_flow_data, flow_id_map)
                new_sf, new_sf_version = await create_flow(
                    user, org_id, sf_name, sf_flow_data, sf_description,
                )
                if old_id:
                    flow_id_map[old_id] = str(new_sf.id)
                created_subflows.append((new_sf, new_sf_version))
                print(f"[IMPORT] ✓ subflow créé : {sf_name!r} → {new_sf.id}")
        except Exception as exc:
            print(f"[IMPORT] ✗ erreur subflow {sf_name!r} : {exc!r}")

    # ── 3. Mettre à jour flow_data principal ─────────────────────
    _update_agent_refs(flow_data)
    _update_flow_id_refs(flow_data, flow_id_map)

    # ── 4. Créer le flow principal ────────────────────────────────
    print(f"[IMPORT] → création flow principal : {name!r}")
    main_flow, main_version = await create_flow(user, org_id, name, flow_data, description)
    print(f"[IMPORT] ✓ flow principal créé : {name!r} → {main_flow.id}")

    # ── 5. Retourner tous les flows créés (subflows + principal) ──
    # Les subflows sont retournés dans l'ordre naturel (parent avant enfant)
    # pour faciliter l'affichage côté frontend.
    all_created = list(reversed(created_subflows)) + [(main_flow, main_version)]
    print(f"[IMPORT] ✓ total flows créés : {len(all_created)}")
    return all_created
