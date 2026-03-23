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
from app.features.auth.models import User
from app.features.flows.models import Flow, FlowShare, FlowVersion


async def _get_flow_for_org(flow_id: str, org_id: str) -> Flow:
    """Récupère un flow et vérifie qu'il appartient à l'organisation."""
    flow = await Flow.get(PydanticObjectId(flow_id))
    if not flow:
        raise NotFoundError("Flow non trouvé")
    if str(flow.organization_id) != org_id:
        raise NotFoundError("Flow non trouvé dans cette organisation")
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
):
    """Liste tous les flows d'une organisation (pagine)."""
    from app.core.pagination import paginate

    await check_org_membership(user, org_id)

    query = Flow.find(Flow.organization_id == PydanticObjectId(org_id))
    return await paginate(query, page, page_size, sort_field="-created_at")


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
    """Supprime un flow et toutes ses versions."""
    await check_org_membership(user, org_id)
    flow = await _get_flow_for_org(flow_id, org_id)

    # Suppression cascade des versions
    await FlowVersion.find(FlowVersion.flow_id == flow.id).delete()
    await flow.delete()


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

    from app.features.organizations.models import Organization

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


async def list_shared_flows(user: User, org_id: str) -> list[Flow]:
    """Liste les flows partagés avec mon organisation (lecture seule)."""
    await check_org_membership(user, org_id)

    shares = await FlowShare.find(
        FlowShare.shared_with_org_id == PydanticObjectId(org_id),
    ).to_list()

    flow_ids = [s.flow_id for s in shares]
    if not flow_ids:
        return []

    return await Flow.find({"_id": {"$in": flow_ids}}).sort("-created_at").to_list()


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
