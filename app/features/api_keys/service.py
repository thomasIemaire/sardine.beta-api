"""
Service clés API.
Gestion de la création, listing, révocation et suppression
des clés API d'une organisation.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.enums import Status, TeamMemberRole
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.pagination import PaginatedResponse, paginate
from app.features.api_keys.models import ApiKey
from app.features.api_keys.schemas import ApiKeyCreate
from app.features.auth.models import User
from app.features.organizations.models import Organization
from app.features.teams.models import Team, TeamMember


# ─── Helpers ─────────────────────────────────────────────────────

def _generate_token() -> str:
    """Génère un token aléatoire de 48 caractères alphanumériques préfixé par srd_."""
    raw = secrets.token_hex(24)  # 48 hex chars
    return f"srd_{raw}"


def _hash_token(token: str) -> str:
    """Hash SHA-256 du token complet."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _get_org_or_404(org_id: str) -> Organization:
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    return org


async def _assert_org_admin(user: User, org: Organization) -> None:
    """Vérifie que l'utilisateur est propriétaire de l'organisation."""
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError("Seul un administrateur de l'organisation peut effectuer cette action")


async def _assert_org_member(user: User, org: Organization) -> None:
    """Vérifie que l'utilisateur est membre de l'organisation (owner ou membre d'une équipe)."""
    if str(org.owner_id) == str(user.id):
        return

    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if root_team:
        membership = await TeamMember.find_one(
            TeamMember.team_id == root_team.id,
            TeamMember.user_id == user.id,
            TeamMember.status == Status.ACTIVE,
        )
        if membership:
            return

    raise ForbiddenError("Vous n'êtes pas membre de cette organisation")


# ─── CRUD ────────────────────────────────────────────────────────

async def create_api_key(user: User, org_id: str, payload: ApiKeyCreate) -> tuple[ApiKey, str]:
    """
    Crée une nouvelle clé API pour l'organisation.
    Retourne le document ApiKey et le token en clair (unique occasion).
    """
    org = await _get_org_or_404(org_id)
    await _assert_org_admin(user, org)

    token = _generate_token()
    prefix = token[:12]
    hashed = _hash_token(token)

    api_key = ApiKey(
        organization_id=org.id,
        name=payload.name,
        prefix=prefix,
        hashed_key=hashed,
        status=1,
        created_by=user.id,
    )
    await api_key.insert()

    await log_action(
        user_id=user.id,
        action="API_KEY_CREATE",
        details=f"Clé API « {payload.name} » créée (préfixe : {prefix})",
        organization_id=org.id,
    )

    return api_key, token


async def list_api_keys(
    user: User,
    org_id: str,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse:
    """Liste paginée des clés API d'une organisation."""
    org = await _get_org_or_404(org_id)
    await _assert_org_member(user, org)

    query = ApiKey.find(ApiKey.organization_id == org.id)
    return await paginate(query, page=page, page_size=page_size)


async def revoke_api_key(user: User, org_id: str, key_id: str) -> ApiKey:
    """Révoque une clé API active."""
    org = await _get_org_or_404(org_id)
    await _assert_org_admin(user, org)

    api_key = await ApiKey.get(PydanticObjectId(key_id))
    if not api_key or str(api_key.organization_id) != str(org.id):
        raise NotFoundError("Clé API non trouvée")

    if api_key.status == 0:
        raise ConflictError("Cette clé est déjà révoquée")

    api_key.status = 0
    api_key.updated_at = datetime.now(timezone.utc)
    await api_key.save()

    await log_action(
        user_id=user.id,
        action="API_KEY_REVOKE",
        details=f"Clé API « {api_key.name} » révoquée (préfixe : {api_key.prefix})",
        organization_id=org.id,
    )

    return api_key


async def delete_api_key(user: User, org_id: str, key_id: str) -> None:
    """Supprime définitivement une clé API."""
    org = await _get_org_or_404(org_id)
    await _assert_org_admin(user, org)

    api_key = await ApiKey.get(PydanticObjectId(key_id))
    if not api_key or str(api_key.organization_id) != str(org.id):
        raise NotFoundError("Clé API non trouvée")

    was_active = api_key.status == 1

    await api_key.delete()

    if was_active:
        await log_action(
            user_id=user.id,
            action="API_KEY_DELETE",
            details=f"Clé API « {api_key.name} » supprimée (préfixe : {api_key.prefix})",
            organization_id=org.id,
        )


# ─── Authentification par clé API ────────────────────────────────

async def authenticate_api_key(token: str) -> tuple[ApiKey, Organization] | None:
    """
    Authentifie une requête via clé API.
    1. Extraire le préfixe pour pré-filtrer en base
    2. Hasher le token et comparer au hashed_key
    3. Vérifier que la clé est active
    Retourne (ApiKey, Organization) ou None si invalide.
    """
    if not token.startswith("srd_"):
        return None

    prefix = token[:12]
    candidates = await ApiKey.find(ApiKey.prefix == prefix, ApiKey.status == 1).to_list()

    hashed = _hash_token(token)
    for key in candidates:
        if key.hashed_key == hashed:
            org = await Organization.get(key.organization_id)
            if org:
                return key, org
            break

    return None
