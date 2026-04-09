"""
Point d'entrée de l'application FastAPI.
Gère le lifespan (init DB + background task de purge corbeille)
et le montage de tous les routers par feature.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db

logger = logging.getLogger(__name__)


async def _trash_purge_loop() -> None:
    """
    Tâche de fond qui purge les éléments de la corbeille
    dont la rétention de 30 jours est expirée.
    S'exécute périodiquement selon TRASH_PURGE_INTERVAL_HOURS.
    """
    from app.features.folders.service import purge_expired_trash

    while True:
        try:
            count = await purge_expired_trash()
            if count > 0:
                logger.info("Trash purge completed: %d items removed", count)
        except Exception:
            logger.exception("Error during trash purge")

        # Attente avant la prochaine exécution
        await asyncio.sleep(settings.TRASH_PURGE_INTERVAL_HOURS * 3600)


async def _notification_purge_loop() -> None:
    """
    Tâche de fond qui purge les notifications info lues
    depuis plus de 30 jours. S'exécute toutes les 24h.
    """
    from app.features.notifications.service import purge_old_read_notifications

    while True:
        try:
            count = await purge_old_read_notifications(days=30)
            if count > 0:
                logger.info("Notification purge completed: %d items removed", count)
        except Exception:
            logger.exception("Error during notification purge")

        await asyncio.sleep(24 * 3600)


async def _file_purge_loop() -> None:
    """
    Tâche de fond qui purge les fichiers en corbeille
    dont la rétention de 30 jours est expirée. S'exécute toutes les 24h.
    """
    from app.features.files.service import purge_expired_file_trash

    while True:
        try:
            count = await purge_expired_file_trash()
            if count > 0:
                logger.info("File trash purge completed: %d files removed", count)
        except Exception:
            logger.exception("Error during file trash purge")

        await asyncio.sleep(24 * 3600)


async def _flow_purge_loop() -> None:
    """
    Tâche de fond qui purge les flows en corbeille
    dont la rétention de 30 jours est expirée. S'exécute toutes les 24h.
    """
    from app.features.flows.service import purge_expired_flow_trash

    while True:
        try:
            count = await purge_expired_flow_trash(days=30)
            if count > 0:
                logger.info("Flow trash purge completed: %d flows removed", count)
        except Exception:
            logger.exception("Error during flow trash purge")

        await asyncio.sleep(24 * 3600)


async def _agent_purge_loop() -> None:
    """
    Tâche de fond qui purge les agents en corbeille
    dont la rétention de 30 jours est expirée. S'exécute toutes les 24h.
    """
    from app.features.agents.service import purge_expired_agent_trash

    while True:
        try:
            count = await purge_expired_agent_trash(days=30)
            if count > 0:
                logger.info("Agent trash purge completed: %d agents removed", count)
        except Exception:
            logger.exception("Error during agent trash purge")

        await asyncio.sleep(24 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Lifespan : initialise la DB au démarrage et lance la purge
    périodique de la corbeille en arrière-plan.
    """
    await init_db()
    logger.info("Database initialized — MongoDB: %s", settings.MONGODB_NAME)

    # Lancement des tâches de fond
    purge_task = asyncio.create_task(_trash_purge_loop())
    notif_purge_task = asyncio.create_task(_notification_purge_loop())
    file_purge_task = asyncio.create_task(_file_purge_loop())
    flow_purge_task = asyncio.create_task(_flow_purge_loop())
    agent_purge_task = asyncio.create_task(_agent_purge_loop())

    yield

    # Arrêt propre des tâches de fond
    purge_task.cancel()
    notif_purge_task.cancel()
    file_purge_task.cancel()
    flow_purge_task.cancel()
    agent_purge_task.cancel()
    for task in (purge_task, notif_purge_task, file_purge_task, flow_purge_task, agent_purge_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Sardine Beta API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — à restreindre en production avec les origines spécifiques
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Fichiers statiques (avatars, etc.) ──────────────────────────
from pathlib import Path

storage_dir = Path("storage")
storage_dir.mkdir(exist_ok=True)
(storage_dir / "avatars").mkdir(exist_ok=True)
(storage_dir / "files").mkdir(exist_ok=True)
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

_static_app = StaticFiles(directory="storage")


class NoCacheStaticFiles:
    """Wrapper qui ajoute Cache-Control: no-cache sur les fichiers statiques."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        async def send_with_no_cache(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"no-cache"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_no_cache)


app.mount("/storage", NoCacheStaticFiles(_static_app), name="storage")

# ─── Montage des routers par feature ─────────────────────────────

from app.features.api_keys.router import router as api_keys_router
from app.features.agents.router import router as agents_router
from app.features.audit.router import router as audit_router
from app.features.auth.router import router as auth_router
from app.features.files.router import router as files_router
from app.features.flows.router import router as flows_router
from app.features.flows.execution_router import router as flows_execution_router
from app.features.folders.router import router as folders_router
from app.features.notifications.router import router as notifications_router
from app.features.organizations.router import router as organizations_router
from app.features.files.tags_router import router as tags_router
from app.features.permissions.router import router as permissions_router
from app.features.search.router import router as search_router
from app.features.teams.router import router as teams_router
from app.features.users.router import router as users_router

app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(organizations_router, prefix="/api")
app.include_router(folders_router, prefix="/api")
app.include_router(teams_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(flows_router, prefix="/api")
app.include_router(flows_execution_router, prefix="/api")
app.include_router(permissions_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(tags_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(api_keys_router, prefix="/api")


@app.get("/health", tags=["Health"])
async def health_check():
    """Endpoint de santé pour les healthchecks infra."""
    return {"status": "ok", "environment": settings.ENVIRONMENT}


# ─── Handler approbation flow ─────────────────────────────────────

async def _handle_flow_approval(notif, action_key: str) -> str:
    """
    Handler appelé quand l'utilisateur répond à une notification d'approbation.
    Reprend l'exécution du flow avec la valeur choisie.
    """
    import asyncio
    from app.core.enums import NotificationActionStatus
    from app.features.flows.engine import FlowEngine, register_execution
    from app.features.flows.models import ApprovalTask

    approval_task_id = notif.action_payload.get("approval_task_id")
    execution_id = notif.action_payload.get("execution_id")

    if not approval_task_id or not execution_id:
        return NotificationActionStatus.REJECTED

    # Mettre à jour l'ApprovalTask
    from beanie import PydanticObjectId
    task = await ApprovalTask.get(PydanticObjectId(approval_task_id))
    if task and task.status == "pending":
        from datetime import UTC, datetime
        from app.features.auth.models import User
        user = await User.get(PydanticObjectId(str(notif.recipient_user_id)))
        await task.set({
            "status": "responded",
            "response": action_key,
            "response_label": action_key,
            "responded_by": notif.recipient_user_id,
            "responded_at": datetime.now(UTC),
        })

    # Reprendre l'exécution du flow en arrière-plan
    engine = FlowEngine()
    t = asyncio.create_task(engine.resume(execution_id, action_key))
    register_execution(execution_id, t)

    return NotificationActionStatus.ACCEPTED


from app.features.notifications.service import register_action_handler
register_action_handler("flow_approval", _handle_flow_approval)
