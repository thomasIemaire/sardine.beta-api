"""
Enums métier partagés par toute l'application.
Les valeurs numériques correspondent aux spécifications fonctionnelles :
  - Role: 0 = Admin, 1 = Utilisateur
  - Status: 0 = Inactif, 1 = Actif
  - TeamMemberRole: 1 = Propriétaire, 2 = Membre
  - NotificationType: info, action
  - NotificationActionStatus: pending, accepted, rejected
  - FlowStatus: active, error, pending
"""

from enum import IntEnum, StrEnum


class UserRole(IntEnum):
    ADMIN = 0
    USER = 1


class Status(IntEnum):
    INACTIVE = 0
    ACTIVE = 1


class TeamMemberRole(IntEnum):
    OWNER = 1
    MEMBER = 2


class NotificationType(StrEnum):
    """Type de notification : simple info ou action requise."""
    INFO = "info"
    ACTION = "action"


class FlowStatus(StrEnum):
    """Statut d'un flow."""
    ACTIVE = "active"
    ERROR = "error"
    PENDING = "pending"


class NotificationActionStatus(StrEnum):
    """Statut d'une action liée à une notification."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# Labels lisibles pour l'affichage côté client
USER_ROLE_LABELS = {UserRole.ADMIN: "Administrateur", UserRole.USER: "Utilisateur"}
STATUS_LABELS = {Status.ACTIVE: "Actif", Status.INACTIVE: "Inactif"}
TEAM_ROLE_LABELS = {TeamMemberRole.OWNER: "Propriétaire", TeamMemberRole.MEMBER: "Membre"}
FLOW_STATUS_LABELS = {
    FlowStatus.ACTIVE: "Actif",
    FlowStatus.ERROR: "Erreur",
    FlowStatus.PENDING: "En attente",
}
NOTIFICATION_TYPE_LABELS = {
    NotificationType.INFO: "Information",
    NotificationType.ACTION: "Action requise",
}
NOTIFICATION_ACTION_STATUS_LABELS = {
    NotificationActionStatus.PENDING: "En attente",
    NotificationActionStatus.ACCEPTED: "Acceptée",
    NotificationActionStatus.REJECTED: "Refusée",
}
