"""
Service notifications — création, consultation, marquage et résolution.

La création de notification pousse automatiquement le message
via WebSocket si l'utilisateur est connecté.
"""

from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from app.core.enums import NotificationActionStatus, NotificationType
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.features.notifications.models import Notification
from app.features.notifications.ws_manager import ws_manager

# ─── Création ────────────────────────────────────────────────────

async def create_info_notification(
    recipient_user_id: str,
    title: str,
    message: str,
    organization_id: str | None = None,
) -> Notification:
    """Crée une notification de type info (simple message)."""
    notif = Notification(
        recipient_user_id=PydanticObjectId(recipient_user_id),
        organization_id=PydanticObjectId(organization_id) if organization_id else None,
        type=NotificationType.INFO,
        title=title,
        message=message,
    )
    await notif.insert()
    await _push_via_ws(notif)
    return notif


async def create_action_notification(
    recipient_user_id: str,
    title: str,
    message: str,
    actions: list[dict],
    action_payload: dict,
    organization_id: str | None = None,
) -> Notification:
    """
    Crée une notification de type action.
    `actions` est une liste de dicts {"key": str, "label": str}.
    `action_payload` contient les données nécessaires au traitement.
    """
    notif = Notification(
        recipient_user_id=PydanticObjectId(recipient_user_id),
        organization_id=PydanticObjectId(organization_id) if organization_id else None,
        type=NotificationType.ACTION,
        title=title,
        message=message,
        actions=actions,
        action_payload=action_payload,
        action_status=NotificationActionStatus.PENDING,
    )
    await notif.insert()
    await _push_via_ws(notif)
    return notif


# ─── Consultation ────────────────────────────────────────────────

async def list_notifications(
    user_id: str,
    scope: str = "all",
    notification_type: str = "all",
    organization_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    """
    Liste les notifications d'un utilisateur avec filtres et pagination.
    scope: "user" (perso uniquement) | "organization" (org active) | "all"
    notification_type: "info" | "action" | "all"
    """
    from app.core.pagination import paginate

    filters = {"recipient_user_id": PydanticObjectId(user_id)}

    # Filtre par scope
    if scope == "user":
        filters["organization_id"] = None
    elif scope == "organization":
        if not organization_id:
            raise ValidationError("organization_id requis pour le scope 'organization'")
        filters["organization_id"] = PydanticObjectId(organization_id)

    # Filtre par type
    if notification_type != "all":
        if notification_type not in (NotificationType.INFO, NotificationType.ACTION):
            raise ValidationError("Type invalide : 'info', 'action' ou 'all'")
        filters["type"] = notification_type

    query = Notification.find(filters)
    return await paginate(query, page, page_size, sort_field="-created_at")


async def get_unread_count(
    user_id: str,
    organization_id: str | None = None,
) -> dict:
    """Compteur de notifications non lues (total, info, action)."""
    uid = PydanticObjectId(user_id)

    base_filter = {"recipient_user_id": uid, "is_read": False}

    # Si une org est fournie, on compte user + org ; sinon tout
    if organization_id:
        org_oid = PydanticObjectId(organization_id)
        all_unread = await Notification.find(
            {"recipient_user_id": uid, "is_read": False,
             "$or": [{"organization_id": None}, {"organization_id": org_oid}]}
        ).to_list()
    else:
        all_unread = await Notification.find(base_filter).to_list()

    info_count = sum(1 for n in all_unread if n.type == NotificationType.INFO)
    action_count = sum(1 for n in all_unread if n.type == NotificationType.ACTION)

    return {"total": len(all_unread), "info": info_count, "action": action_count}


# ─── Marquage comme lu ──────────────────────────────────────────

async def mark_as_read(user_id: str, notification_id: str) -> Notification:
    """Marque une notification comme lue."""
    notif = await Notification.get(PydanticObjectId(notification_id))
    if not notif:
        raise NotFoundError("Notification non trouvée")
    if str(notif.recipient_user_id) != user_id:
        raise ForbiddenError("Cette notification ne vous appartient pas")

    now = datetime.now(UTC)
    await notif.set({"is_read": True, "read_at": now})
    return notif


async def mark_all_as_read(
    user_id: str,
    scope: str = "all",
    organization_id: str | None = None,
) -> int:
    """
    Marque toutes les notifications non lues comme lues.
    Retourne le nombre de notifications mises à jour.
    """
    filters = {
        "recipient_user_id": PydanticObjectId(user_id),
        "is_read": False,
    }

    if scope == "user":
        filters["organization_id"] = None
    elif scope == "organization":
        if not organization_id:
            raise ValidationError("organization_id requis pour le scope 'organization'")
        filters["organization_id"] = PydanticObjectId(organization_id)

    now = datetime.now(UTC)
    result = await Notification.find(filters).update_many(
        {"$set": {"is_read": True, "read_at": now}}
    )
    return result.modified_count if result else 0


# ─── Résolution d'action ────────────────────────────────────────

async def resolve_action(
    user_id: str, notification_id: str, action_key: str,
) -> Notification:
    """
    Résout une notification action en enregistrant la clé d'action choisie.
    Retourne la notification mise à jour.
    Le traitement métier (ex: accepter une invitation) est délégué
    aux action handlers enregistrés.
    """
    notif = await Notification.get(PydanticObjectId(notification_id))
    if not notif:
        raise NotFoundError("Notification non trouvée")
    if str(notif.recipient_user_id) != user_id:
        raise ForbiddenError("Cette notification ne vous appartient pas")
    if notif.type != NotificationType.ACTION:
        raise ValidationError("Cette notification n'est pas de type action")
    if notif.action_status != NotificationActionStatus.PENDING:
        raise ValidationError("Cette notification a déjà été traitée")

    # Vérifier que la clé d'action est valide
    valid_keys = {a["key"] for a in notif.actions}
    if action_key not in valid_keys:
        raise ValidationError(f"Action invalide. Actions possibles : {', '.join(valid_keys)}")

    # Appeler le handler métier
    new_status = await _execute_action_handler(notif, action_key)

    # Mettre à jour la notification
    now = datetime.now(UTC)
    await notif.set({
        "action_status": new_status,
        "resolved_action_key": action_key,
        "is_read": True,
        "read_at": now,
    })
    return notif


# ─── Suppression ─────────────────────────────────────────────────

async def delete_notification(user_id: str, notification_id: str) -> None:
    """Supprime une notification."""
    notif = await Notification.get(PydanticObjectId(notification_id))
    if not notif:
        raise NotFoundError("Notification non trouvée")
    if str(notif.recipient_user_id) != user_id:
        raise ForbiddenError("Cette notification ne vous appartient pas")
    await notif.delete()


# ─── Purge automatique ──────────────────────────────────────────

async def purge_old_read_notifications(days: int = 30) -> int:
    """
    Supprime les notifications info lues depuis plus de `days` jours.
    Les notifications action sont conservées (historique des décisions).
    """
    cutoff = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ) - timedelta(days=days)

    result = await Notification.find(
        Notification.type == NotificationType.INFO,
        Notification.is_read == True,  # noqa: E712
        Notification.read_at != None,  # noqa: E711
        {"read_at": {"$lt": cutoff}},
    ).delete()
    return result.deleted_count if result else 0


# ─── Action handlers (registre) ─────────────────────────────────

# Registre des handlers d'action : action_type → callable
# Chaque handler reçoit (notification, action_key) et retourne le nouveau statut
_action_handlers: dict[str, callable] = {}


def register_action_handler(action_type: str, handler: callable) -> None:
    """Enregistre un handler pour un type d'action donné."""
    _action_handlers[action_type] = handler


async def _execute_action_handler(
    notif: Notification, action_key: str,
) -> str:
    """
    Exécute le handler métier correspondant au type d'action.
    Si aucun handler n'est enregistré, on met simplement le statut
    en fonction de la clé (accept → accepted, sinon rejected).
    """
    action_type = notif.action_payload.get("action_type")

    if action_type and action_type in _action_handlers:
        return await _action_handlers[action_type](notif, action_key)

    # Comportement par défaut : accept → accepted, autre → rejected
    if action_key == "accept":
        return NotificationActionStatus.ACCEPTED
    return NotificationActionStatus.REJECTED


# ─── WebSocket push ─────────────────────────────────────────────

async def _push_via_ws(notif: Notification) -> None:
    """Envoie la notification en temps réel via WebSocket."""
    from app.features.notifications.schemas import NotificationRead

    user_id = str(notif.recipient_user_id)
    if ws_manager.is_connected(user_id):
        payload = NotificationRead.from_notification(notif).model_dump()
        await ws_manager.send_to_user(user_id, {
            "event": "notification",
            "data": payload,
        })
