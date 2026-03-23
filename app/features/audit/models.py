"""
Modèle AuditLog — journalisation des actions sensibles.
Stocke : date, utilisateur, action, détails.
Actions concernées : connexion, déconnexion, modification rôle/statut,
suppression dossier/document/équipe.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed
from pydantic import Field


class AuditLog(Document):
    user_id: Indexed(str, unique=False) | None = None
    organization_id: Indexed(str, unique=False) | None = None
    action: Indexed(str, unique=False)
    details: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "audit_logs"
