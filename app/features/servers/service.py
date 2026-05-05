"""
Service serveurs.
Gère le CRUD des serveurs configurés et la récupération des stats live
avec cache court pour éviter de spammer le serveur cible.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx
from beanie import PydanticObjectId

from app.config import settings
from app.core.audit import log_action
from app.core.enums import ServerHealth, ServerType
from app.core.exceptions import ConflictError, NotFoundError
from app.features.auth.models import User
from app.features.servers.client import fetch_stats_payload
from app.features.servers.models import Server
from app.features.servers.schemas import ServerCreate, ServerStats, ServerUpdate

logger = logging.getLogger(__name__)

# Cache des stats par serveur — TTL court pour éviter de spammer
# le serveur cible quand plusieurs admins regardent la page en même temps.
_STATS_CACHE_TTL_SECONDS = 2.0
_stats_cache: dict[str, tuple[float, ServerStats]] = {}
_stats_locks: dict[str, asyncio.Lock] = {}


# ─── Helpers ─────────────────────────────────────────────────────


async def _get_server_or_404(server_id: str) -> Server:
    try:
        oid = PydanticObjectId(server_id)
    except Exception as exc:
        raise NotFoundError("Serveur non trouvé") from exc
    server = await Server.get(oid)
    if not server:
        raise NotFoundError("Serveur non trouvé")
    return server


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


# ─── CRUD ────────────────────────────────────────────────────────


async def create_server(user: User, payload: ServerCreate) -> Server:
    existing = await Server.find_one(Server.name == payload.name)
    if existing:
        raise ConflictError("Un serveur avec ce nom existe déjà")

    server = Server(
        name=payload.name,
        type=payload.type,
        base_url=_normalize_url(str(payload.base_url)),
        api_key=payload.api_key,
        enabled=payload.enabled,
        description=payload.description,
    )
    await server.insert()

    await log_action(
        user_id=user.id,
        action="SERVER_CREATE",
        details=f"Serveur « {server.name} » créé ({server.type})",
    )
    return server


async def list_servers() -> list[Server]:
    return await Server.find_all().sort("+created_at").to_list()


async def update_server(user: User, server_id: str, payload: ServerUpdate) -> Server:
    server = await _get_server_or_404(server_id)

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != server.name:
        clash = await Server.find_one(Server.name == data["name"])
        if clash:
            raise ConflictError("Un serveur avec ce nom existe déjà")
    if "base_url" in data and data["base_url"] is not None:
        data["base_url"] = _normalize_url(str(data["base_url"]))

    for key, value in data.items():
        setattr(server, key, value)
    server.updated_at = datetime.now(UTC)
    await server.save()

    # Invalide le cache pour ce serveur (URL/clé peuvent avoir changé)
    _stats_cache.pop(str(server.id), None)

    await log_action(
        user_id=user.id,
        action="SERVER_UPDATE",
        details=f"Serveur « {server.name} » mis à jour",
    )
    return server


async def delete_server(user: User, server_id: str) -> None:
    server = await _get_server_or_404(server_id)
    name = server.name
    await server.delete()
    _stats_cache.pop(server_id, None)

    await log_action(
        user_id=user.id,
        action="SERVER_DELETE",
        details=f"Serveur « {name} » supprimé",
    )


# ─── Stats ───────────────────────────────────────────────────────


async def fetch_server_stats(server: Server, *, use_cache: bool = True) -> ServerStats:
    """
    Récupère les stats d'un serveur avec cache court (~2s).
    Retourne toujours un ServerStats — health=UNREACHABLE/ERROR si l'appel échoue.
    """
    sid = str(server.id)
    now_mono = time.monotonic()

    if use_cache:
        cached = _stats_cache.get(sid)
        if cached and (now_mono - cached[0]) < _STATS_CACHE_TTL_SECONDS:
            return cached[1]

    # Verrou par serveur : si plusieurs requêtes arrivent en même temps,
    # une seule fait l'appel HTTP et les autres récupèrent le cache.
    lock = _stats_locks.setdefault(sid, asyncio.Lock())
    async with lock:
        cached = _stats_cache.get(sid)
        if use_cache and cached and (time.monotonic() - cached[0]) < _STATS_CACHE_TTL_SECONDS:
            return cached[1]

        fetched_at = datetime.now(UTC)
        if not server.enabled:
            stats = ServerStats.unreachable(
                server,
                error="Serveur désactivé",
                fetched_at=fetched_at,
                health=ServerHealth.UNREACHABLE,
            )
            _stats_cache[sid] = (time.monotonic(), stats)
            return stats

        t0 = time.monotonic()
        try:
            payload = await fetch_stats_payload(server)
        except httpx.TimeoutException:
            stats = ServerStats.unreachable(
                server, error="Timeout", fetched_at=fetched_at,
            )
        except httpx.HTTPStatusError as exc:
            stats = ServerStats.unreachable(
                server,
                error=f"HTTP {exc.response.status_code}",
                fetched_at=fetched_at,
                health=ServerHealth.ERROR,
            )
        except httpx.HTTPError as exc:
            stats = ServerStats.unreachable(
                server, error=str(exc), fetched_at=fetched_at,
            )
        except Exception as exc:
            logger.exception("Erreur inattendue fetch stats serveur %s", sid)
            stats = ServerStats.unreachable(
                server,
                error=f"Erreur interne: {exc}",
                fetched_at=fetched_at,
                health=ServerHealth.ERROR,
            )
        else:
            latency_ms = (time.monotonic() - t0) * 1000
            stats = ServerStats.from_payload(
                server, payload, fetched_at=fetched_at, latency_ms=latency_ms,
            )

        _stats_cache[sid] = (time.monotonic(), stats)
        return stats


async def fetch_all_stats() -> list[ServerStats]:
    """Récupère les stats de tous les serveurs en parallèle."""
    servers = await list_servers()
    if not servers:
        return []
    return await asyncio.gather(*(fetch_server_stats(s) for s in servers))


# ─── Seed ────────────────────────────────────────────────────────


async def seed_default_server_if_empty() -> None:
    """
    Au premier démarrage, si la collection est vide et que GPU_API_BASE_URL
    est configuré dans .env, crée un premier Server pointant dessus.
    Permet la rétrocompatibilité avec l'ancienne config.
    """
    if await Server.find_all().count() > 0:
        return
    base_url = (settings.GPU_API_BASE_URL or "").strip()
    if not base_url:
        return

    server = Server(
        name="GPU RunPod",
        type=ServerType.GPU_RUNPOD,
        base_url=_normalize_url(base_url),
        api_key=settings.GPU_API_KEY,
        enabled=True,
        description="Serveur GPU principal (seed initial depuis .env)",
    )
    await server.insert()
    logger.info("Server seed: « %s » créé depuis GPU_API_BASE_URL", server.name)
