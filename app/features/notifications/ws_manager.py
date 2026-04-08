"""
Gestionnaire de connexions WebSocket.
Maintient la correspondance user_id → liste de WebSockets actifs.
Un même utilisateur peut avoir plusieurs connexions simultanées
(plusieurs onglets, mobile + desktop, etc.).
"""

import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Gestionnaire centralisé des connexions WebSocket par utilisateur."""

    def __init__(self) -> None:
        # user_id (str) → liste de WebSocket actifs
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accepte et enregistre une connexion WebSocket."""
        await websocket.accept()
        self._connections.setdefault(user_id, []).append(websocket)
        count = len(self._connections[user_id])
        logger.info("WebSocket connected: user=%s (total=%d)", user_id, count)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Retire une connexion WebSocket."""
        conns = self._connections.get(user_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns:
            self._connections.pop(user_id, None)
        logger.info("WebSocket disconnected: user=%s", user_id)

    async def send_to_user(self, user_id: str, data: dict) -> None:
        """
        Envoie un message JSON à toutes les connexions actives d'un utilisateur.
        Les connexions mortes sont nettoyées automatiquement.
        """
        conns = self._connections.get(user_id, [])
        dead: list[WebSocket] = []

        for ws in conns:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.append(ws)

        # Nettoyage des connexions mortes
        for ws in dead:
            self.disconnect(user_id, ws)

    async def send_to_users(self, user_ids: list[str], data: dict) -> None:
        """Envoie un message à tous les utilisateurs de la liste."""
        for uid in user_ids:
            if self._connections.get(uid):
                await self.send_to_user(uid, data)

    async def send_to_org(self, org_id: str, data: dict) -> None:
        """
        Broadcast un message à tous les membres actifs d'une organisation
        (owner + équipe racine).
        """
        from app.core.membership import get_org_member_user_ids
        user_ids = await get_org_member_user_ids(org_id)
        await self.send_to_users(user_ids, data)

    def is_connected(self, user_id: str) -> bool:
        """Vérifie si un utilisateur a au moins une connexion active."""
        return bool(self._connections.get(user_id))


# Instance globale unique
ws_manager = ConnectionManager()
