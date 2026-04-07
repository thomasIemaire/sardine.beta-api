"""
Service agents — CRUD, versioning et gestion des branches.

Le versioning fonctionne comme un arbre (DAG) :
chaque version pointe vers son parent via parent_version_id.
L'agent maintient un pointeur active_version_id (HEAD)
vers la version courante.
"""

from datetime import UTC, datetime

from beanie import PydanticObjectId

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.core.membership import check_org_membership
from app.features.agents.models import Agent, AgentShare, AgentVersion
from app.features.auth.models import User


async def _get_agent_for_org(agent_id: str, org_id: str) -> Agent:
    """Récupère un agent et vérifie qu'il appartient à l'organisation."""
    agent = await Agent.get(PydanticObjectId(agent_id))
    if not agent:
        raise NotFoundError("Agent non trouvé")
    if str(agent.organization_id) != org_id:
        raise NotFoundError("Agent non trouvé dans cette organisation")
    return agent


# ─── CRUD Agent ──────────────────────────────────────────────────

async def create_agent(
    user: User, org_id: str, name: str, schema_data: dict,
    description: str = "",
) -> tuple[Agent, AgentVersion]:
    """
    Crée un agent avec sa version initiale.
    Retourne l'agent et la première version.
    """
    await check_org_membership(user, org_id)

    # 1. Créer l'agent (sans version active pour l'instant)
    agent = Agent(
        name=name,
        description=description,
        organization_id=PydanticObjectId(org_id),
        created_by=user.id,
    )
    await agent.insert()

    # 2. Créer la version initiale
    version = AgentVersion(
        agent_id=agent.id,
        schema_data=schema_data,
        parent_version_id=None,
        created_by=user.id,
    )
    await version.insert()

    # 3. Positionner le HEAD
    await agent.set({"active_version_id": version.id})

    return agent, version


async def list_agents(
    user: User, org_id: str, page: int = 1, page_size: int = 20,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    creator: str | None = None,
    origin: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
):
    """Liste tous les agents d'une organisation (pagine, filtrable, triable)."""
    from app.core.filters import build_filters, resolve_sort
    from app.core.pagination import paginate

    await check_org_membership(user, org_id)

    filters = build_filters(
        search=search, creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
    )
    sort_field = resolve_sort(
        sort_by, sort_dir,
        allowed_fields={"name", "created_at", "percentage"},
    )

    query = Agent.find(
        Agent.organization_id == PydanticObjectId(org_id),
        filters,
    )
    return await paginate(query, page, page_size, sort_field=sort_field)


async def get_agent(
    user: User, org_id: str, agent_id: str,
) -> tuple[Agent, dict | None]:
    """
    Détail d'un agent avec le schéma de sa version active.
    Retourne (agent, schema_de_la_version_active).
    """
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    active_schema = None
    if agent.active_version_id:
        version = await AgentVersion.get(agent.active_version_id)
        if version:
            active_schema = version.schema_data

    return agent, active_schema


async def update_agent(
    user: User, org_id: str, agent_id: str,
    name: str | None = None, description: str | None = None,
) -> Agent:
    """Modifier le nom et/ou la description d'un agent."""
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    update_data: dict = {}
    if name is not None:
        update_data["name"] = name
    if description is not None:
        update_data["description"] = description

    if update_data:
        update_data["updated_at"] = datetime.now(UTC)
        await agent.set(update_data)

    return agent


async def delete_agent(user: User, org_id: str, agent_id: str) -> None:
    """Supprime un agent et toutes ses versions."""
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    # Suppression cascade des versions
    await AgentVersion.find(AgentVersion.agent_id == agent.id).delete()
    await agent.delete()


# ─── Versioning ──────────────────────────────────────────────────

async def create_version(
    user: User,
    org_id: str,
    agent_id: str,
    schema_data: dict,
    parent_version_id: str | None = None,
) -> AgentVersion:
    """
    Crée une nouvelle version de l'agent.
    Si parent_version_id n'est pas fourni, branche depuis la version active.
    Auto-checkout : la nouvelle version devient la version active.
    """
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    # Résoudre le parent
    if parent_version_id:
        parent = await AgentVersion.get(PydanticObjectId(parent_version_id))
        if not parent or str(parent.agent_id) != str(agent.id):
            raise NotFoundError("Version parente non trouvée pour cet agent")
        resolved_parent_id = parent.id
    elif agent.active_version_id:
        resolved_parent_id = agent.active_version_id
    else:
        raise ValidationError("Aucune version parente disponible")

    # Créer la nouvelle version
    version = AgentVersion(
        agent_id=agent.id,
        schema_data=schema_data,
        parent_version_id=resolved_parent_id,
        created_by=user.id,
    )
    await version.insert()

    # Auto-checkout
    await agent.set({
        "active_version_id": version.id,
        "updated_at": datetime.now(UTC),
    })

    return version


async def list_versions(
    user: User, org_id: str, agent_id: str,
) -> list[AgentVersion]:
    """Liste toutes les versions d'un agent (arbre complet)."""
    await check_org_membership(user, org_id)
    await _get_agent_for_org(agent_id, org_id)

    return await AgentVersion.find(
        AgentVersion.agent_id == PydanticObjectId(agent_id),
    ).sort("+created_at").to_list()


async def get_version(
    user: User, org_id: str, agent_id: str, version_id: str,
) -> AgentVersion:
    """Détail d'une version spécifique."""
    await check_org_membership(user, org_id)
    await _get_agent_for_org(agent_id, org_id)

    version = await AgentVersion.get(PydanticObjectId(version_id))
    if not version or str(version.agent_id) != agent_id:
        raise NotFoundError("Version non trouvée pour cet agent")

    return version


async def switch_active_version(
    user: User, org_id: str, agent_id: str, version_id: str,
) -> Agent:
    """Change la version active de l'agent (checkout)."""
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    # Vérifier que la version existe et appartient à cet agent
    version = await AgentVersion.get(PydanticObjectId(version_id))
    if not version or str(version.agent_id) != str(agent.id):
        raise NotFoundError("Version non trouvée pour cet agent")

    await agent.set({
        "active_version_id": version.id,
        "updated_at": datetime.now(UTC),
    })
    return agent


async def get_version_history(
    user: User, org_id: str, agent_id: str, version_id: str,
) -> list[AgentVersion]:
    """
    Historique d'une version : remonte la chaîne des ancêtres
    jusqu'à la version initiale (git log).
    Retourne la liste ordonnée de la version demandée
    jusqu'à la racine (version initiale en dernier).
    """
    await check_org_membership(user, org_id)
    await _get_agent_for_org(agent_id, org_id)

    version = await AgentVersion.get(PydanticObjectId(version_id))
    if not version or str(version.agent_id) != agent_id:
        raise NotFoundError("Version non trouvée pour cet agent")

    history = [version]
    current = version
    max_depth = 100  # Sécurité anti-boucle infinie

    while current.parent_version_id and len(history) < max_depth:
        parent = await AgentVersion.get(current.parent_version_id)
        if not parent:
            break
        # Stopper a la frontiere de l'agent (ne pas traverser les forks)
        if str(parent.agent_id) != agent_id:
            break
        history.append(parent)
        current = parent

    return history


# ─── Partage ─────────────────────────────────────────────────────

async def share_agent(
    user: User, org_id: str, agent_id: str, target_org_ids: list[str],
) -> list[AgentShare]:
    """
    Partage un agent en lecture seule avec une ou plusieurs organisations.
    Seul un membre de l'org propriétaire peut partager.
    Les orgs déjà partagées sont ignorées silencieusement.
    """
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    from app.core.membership import get_org_owner_user_ids
    from app.features.notifications.service import create_info_notification
    from app.features.organizations.models import Organization

    # Org source (pour le message)
    source_org = await Organization.get(PydanticObjectId(org_id))
    source_org_name = source_org.name if source_org else "une organisation"

    created: list[AgentShare] = []
    for target_org_id in target_org_ids:
        if org_id == target_org_id:
            continue  # Ignorer sa propre org

        target_org = await Organization.get(PydanticObjectId(target_org_id))
        if not target_org:
            raise NotFoundError(f"Organisation cible {target_org_id} non trouvée")

        existing = await AgentShare.find_one(
            AgentShare.agent_id == agent.id,
            AgentShare.shared_with_org_id == PydanticObjectId(target_org_id),
        )
        if existing:
            continue  # Déjà partagé, on skip

        share = AgentShare(
            agent_id=agent.id,
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
                title="Nouvel agent partagé",
                message=(
                    f"L'organisation « {source_org_name} » a partagé "
                    f"l'agent « {agent.name} » avec votre organisation."
                ),
                organization_id=str(target_org.id),
            )

    return created


async def unshare_agent(
    user: User, org_id: str, agent_id: str, target_org_id: str,
) -> None:
    """Retire le partage d'un agent avec une organisation."""
    await check_org_membership(user, org_id)
    agent = await _get_agent_for_org(agent_id, org_id)

    share = await AgentShare.find_one(
        AgentShare.agent_id == agent.id,
        AgentShare.shared_with_org_id == PydanticObjectId(target_org_id),
    )
    if not share:
        raise NotFoundError("Partage non trouvé")

    await share.delete()


async def list_agent_shares(
    user: User, org_id: str, agent_id: str,
) -> list[AgentShare]:
    """Liste les organisations avec lesquelles un agent est partagé."""
    await check_org_membership(user, org_id)
    await _get_agent_for_org(agent_id, org_id)

    return await AgentShare.find(
        AgentShare.agent_id == PydanticObjectId(agent_id),
    ).to_list()


async def list_shared_agents(
    user: User, org_id: str,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    creator: str | None = None,
    origin: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> list[tuple[Agent, dict | None]]:
    """
    Liste les agents partagés avec mon organisation (lecture seule).
    Retourne chaque agent avec le schema de sa version active.
    """
    from app.core.filters import build_filters, resolve_sort

    await check_org_membership(user, org_id)

    shares = await AgentShare.find(
        AgentShare.shared_with_org_id == PydanticObjectId(org_id),
    ).to_list()

    agent_ids = [s.agent_id for s in shares]
    if not agent_ids:
        return []

    filters = build_filters(
        search=search, creator=creator, origin=origin,
        created_from=created_from, created_to=created_to,
    )
    sort_field = resolve_sort(
        sort_by, sort_dir,
        allowed_fields={"name", "created_at", "percentage"},
    )

    agents = await Agent.find(
        {"_id": {"$in": agent_ids}},
        filters,
    ).sort(sort_field).to_list()

    result = []
    for agent in agents:
        active_schema = None
        if agent.active_version_id:
            version = await AgentVersion.get(agent.active_version_id)
            if version:
                active_schema = version.schema_data
        result.append((agent, active_schema))

    return result


async def get_shared_agent(
    user: User, org_id: str, agent_id: str,
) -> tuple[Agent, dict | None]:
    """
    Détail d'un agent partagé avec mon organisation (lecture seule).
    Vérifie que le partage existe.
    """
    await check_org_membership(user, org_id)

    agent = await Agent.get(PydanticObjectId(agent_id))
    if not agent:
        raise NotFoundError("Agent non trouvé")

    share = await AgentShare.find_one(
        AgentShare.agent_id == agent.id,
        AgentShare.shared_with_org_id == PydanticObjectId(org_id),
    )
    if not share:
        raise ForbiddenError("Cet agent n'est pas partagé avec votre organisation")

    active_schema = None
    if agent.active_version_id:
        version = await AgentVersion.get(agent.active_version_id)
        if version:
            active_schema = version.schema_data

    return agent, active_schema


# ─── Fork ────────────────────────────────────────────────────────

async def fork_agent(
    user: User, org_id: str, agent_id: str,
) -> tuple[Agent, AgentVersion]:
    """
    Fork un agent partagé dans mon organisation.
    Crée une copie de l'agent avec la version active de l'original
    comme première version. Le parent_version_id de la copie pointe
    vers la version d'origine (traçabilité).
    """
    await check_org_membership(user, org_id)

    # Vérifier que l'agent source existe
    source_agent = await Agent.get(PydanticObjectId(agent_id))
    if not source_agent:
        raise NotFoundError("Agent source non trouvé")

    # Vérifier que le partage existe (sauf si c'est notre propre org)
    if str(source_agent.organization_id) != org_id:
        share = await AgentShare.find_one(
            AgentShare.agent_id == source_agent.id,
            AgentShare.shared_with_org_id == PydanticObjectId(org_id),
        )
        if not share:
            raise ForbiddenError(
                "Cet agent n'est pas partagé avec votre organisation"
            )

    # Récupérer la version active de l'original
    if not source_agent.active_version_id:
        raise ValidationError("L'agent source n'a pas de version active")
    source_version = await AgentVersion.get(source_agent.active_version_id)
    if not source_version:
        raise ValidationError("Version active de l'agent source introuvable")

    # Créer le fork
    forked_agent = Agent(
        name=source_agent.name,
        description=source_agent.description,
        organization_id=PydanticObjectId(org_id),
        forked_from_id=source_agent.id,
        forked_from_version_id=source_version.id,
        created_by=user.id,
    )
    await forked_agent.insert()

    # Créer la première version du fork (liée à la version source)
    forked_version = AgentVersion(
        agent_id=forked_agent.id,
        schema_data=source_version.schema_data,
        parent_version_id=source_version.id,
        created_by=user.id,
    )
    await forked_version.insert()

    # Positionner le HEAD
    await forked_agent.set({"active_version_id": forked_version.id})

    return forked_agent, forked_version
