"""
Routes notifications (REST + WebSocket).
Toutes les routes REST nécessitent une authentification Bearer.
Le WebSocket s'authentifie via un query parameter `token`.
"""

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_access_token
from app.features.auth.dependencies import CurrentUser
from app.features.auth.models import User
from app.features.auth.schemas import MessageResponse
from app.features.auth.service import is_token_blacklisted
from app.features.notifications.schemas import (
    NotificationRead,
    NotificationResolveAction,
    UnreadCountResponse,
)
from app.features.notifications.service import (
    delete_notification,
    get_unread_count,
    list_notifications,
    mark_all_as_read,
    mark_as_read,
    resolve_action,
)
from app.features.notifications.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ─── REST — Consultation ─────────────────────────────────────────

@router.get("/")
async def get_notifications(
    current_user: CurrentUser,
    scope: str = Query("all", pattern="^(user|organization|all)$"),
    type: str = Query("all", pattern="^(info|action|all)$"),
    organization_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    Liste des notifications de l'utilisateur connecte (pagine).
    Filtres : scope, type, organization_id.
    """
    result = await list_notifications(
        user_id=str(current_user.id),
        scope=scope,
        notification_type=type,
        organization_id=organization_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [NotificationRead.from_notification(n) for n in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    current_user: CurrentUser,
    organization_id: str | None = Query(None),
):
    """Compteur de notifications non lues (total, info, action)."""
    counts = await get_unread_count(
        user_id=str(current_user.id),
        organization_id=organization_id,
    )
    return UnreadCountResponse(**counts)


# ─── REST — Marquage comme lu ───────────────────────────────────

@router.patch("/{notification_id}/read", response_model=NotificationRead)
async def read_notification(notification_id: str, current_user: CurrentUser):
    """Marque une notification comme lue."""
    notif = await mark_as_read(str(current_user.id), notification_id)
    return NotificationRead.from_notification(notif)


@router.patch("/read-all", response_model=MessageResponse)
async def read_all_notifications(
    current_user: CurrentUser,
    scope: str = Query("all", pattern="^(user|organization|all)$"),
    organization_id: str | None = Query(None),
):
    """Marque toutes les notifications non lues comme lues."""
    count = await mark_all_as_read(
        user_id=str(current_user.id),
        scope=scope,
        organization_id=organization_id,
    )
    return MessageResponse(message=f"{count} notification(s) marquée(s) comme lue(s)")


# ─── REST — Résolution d'action ─────────────────────────────────

@router.post("/{notification_id}/resolve", response_model=NotificationRead)
async def resolve_notification_action(
    notification_id: str,
    payload: NotificationResolveAction,
    current_user: CurrentUser,
):
    """
    Résout une notification action en choisissant une des actions proposées.
    Le traitement métier est déclenché automatiquement.
    """
    notif = await resolve_action(
        user_id=str(current_user.id),
        notification_id=notification_id,
        action_key=payload.action_key,
    )
    return NotificationRead.from_notification(notif)


# ─── REST — Suppression ─────────────────────────────────────────

@router.delete("/{notification_id}", response_model=MessageResponse)
async def delete_notif(notification_id: str, current_user: CurrentUser):
    """Supprime une notification."""
    await delete_notification(str(current_user.id), notification_id)
    return MessageResponse(message="Notification supprimée")


# ─── WebSocket ───────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """
    WebSocket pour recevoir les notifications en temps réel.
    Authentification via query parameter `token` (JWT Bearer).
    Connexion : ws://host/api/notifications/ws?token=<jwt>
    """
    # Authentification du token JWT
    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=4001, reason="Token invalide ou expiré")
        return

    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        await websocket.close(code=4001, reason="Token révoqué")
        return

    user_id = payload.get("sub")
    user = await User.get(user_id)
    if user is None:
        await websocket.close(code=4001, reason="Utilisateur non trouvé")
        return

    # Connexion acceptée
    await ws_manager.connect(user_id, websocket)

    try:
        # Boucle de maintien de la connexion
        # Le client peut envoyer des pings ou des messages (ignorés ici)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
    except Exception:
        ws_manager.disconnect(user_id, websocket)
