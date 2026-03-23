"""
Modèle de notification.

Deux types :
  - info  : message simple (lecture seule)
  - action : message + actions génériques que l'utilisateur peut déclencher

Chaque notification est liée à un utilisateur destinataire.
Elle peut optionnellement être rattachée à une organisation
pour le filtrage par scope (user / organization / all).

Les notifications « action » contiennent :
  - actions       : liste d'actions possibles (clé + label)
  - action_payload: données métier nécessaires au traitement de l'action
  - action_status : pending → accepted / rejected / …  (résolu par l'utilisateur)
  - resolved_action_key : la clé de l'action choisie par l'utilisateur
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.core.enums import NotificationType


class NotificationAction(Document):
    """Définition d'une action possible sur une notification."""
    key: str          # identifiant technique (ex: "accept", "reject", "view")
    label: str        # libellé affiché (ex: "Accepter", "Refuser")


class Notification(Document):
    """
    Notification destinée à un utilisateur.
    Peut être de type info (simple message) ou action (message + boutons d'action).
    """
    # Destinataire
    recipient_user_id: Indexed(PydanticObjectId)

    # Rattachement optionnel à une organisation (pour le filtrage par scope)
    organization_id: PydanticObjectId | None = None

    # Contenu
    type: str = Field(default=NotificationType.INFO)
    title: str
    message: str

    # Lecture
    is_read: bool = False

    # ─── Champs spécifiques aux notifications « action » ─────────
    # Liste des actions proposées (ex: [{"key": "accept", "label": "Accepter"}, ...])
    actions: list[dict] = Field(default_factory=list)

    # Payload métier transmis au handler lors de la résolution
    # (ex: {"organization_id": "...", "invitation_type": "org_invite"})
    action_payload: dict = Field(default_factory=dict)

    # Statut de résolution (pending → accepted/rejected)
    action_status: str | None = None

    # Clé de l'action choisie par l'utilisateur
    resolved_action_key: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_at: datetime | None = None

    class Settings:
        name = "notifications"
