"""
Schémas Pydantic pour les notifications.
"""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import (
    NOTIFICATION_ACTION_STATUS_LABELS,
    NOTIFICATION_TYPE_LABELS,
)

# ─── Requêtes ────────────────────────────────────────────────────

class NotificationResolveAction(BaseModel):
    """Résolution d'une notification action par l'utilisateur."""
    action_key: str  # clé de l'action choisie (ex: "accept", "reject")


# ─── Réponses ────────────────────────────────────────────────────

class NotificationActionRead(BaseModel):
    """Action possible sur une notification."""
    key: str
    label: str


class NotificationRead(BaseModel):
    """Réponse de lecture d'une notification."""
    id: str
    type: str
    type_label: str
    title: str
    message: str
    is_read: bool
    organization_id: str | None = None

    # Champs action
    actions: list[NotificationActionRead] = []
    action_status: str | None = None
    action_status_label: str | None = None
    resolved_action_key: str | None = None

    created_at: datetime
    read_at: datetime | None = None

    @classmethod
    def from_notification(cls, notif) -> "NotificationRead":
        action_status_label = None
        if notif.action_status:
            action_status_label = NOTIFICATION_ACTION_STATUS_LABELS.get(
                notif.action_status, notif.action_status,
            )

        return cls(
            id=str(notif.id),
            type=notif.type,
            type_label=NOTIFICATION_TYPE_LABELS.get(notif.type, notif.type),
            title=notif.title,
            message=notif.message,
            is_read=notif.is_read,
            organization_id=str(notif.organization_id) if notif.organization_id else None,
            actions=[
                NotificationActionRead(key=a["key"], label=a["label"])
                for a in notif.actions
            ],
            action_status=notif.action_status,
            action_status_label=action_status_label,
            resolved_action_key=notif.resolved_action_key,
            created_at=notif.created_at,
            read_at=notif.read_at,
        )


class UnreadCountResponse(BaseModel):
    """Compteur de notifications non lues."""
    total: int
    info: int
    action: int
