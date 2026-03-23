"""
Service de journalisation des actions sensibles.
Enregistre : date, utilisateur, action, détails dans la collection audit_logs.
Actions journalisées : connexion, déconnexion, modification de rôle/statut,
suppression de dossier/document/équipe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

logger = logging.getLogger("audit")


async def log_action(
    user_id: PydanticObjectId | str | None,
    action: str,
    details: str = "",
    organization_id: PydanticObjectId | str | None = None,
) -> None:
    """
    Cree une entree d'audit dans MongoDB.
    Import differe pour eviter les imports circulaires au demarrage.
    """
    from app.features.audit.models import AuditLog

    entry = AuditLog(
        user_id=str(user_id) if user_id else None,
        organization_id=str(organization_id) if organization_id else None,
        action=action,
        details=details,
        created_at=datetime.now(timezone.utc),
    )
    await entry.insert()

    logger.info("AUDIT | user=%s | action=%s | %s", user_id, action, details)
