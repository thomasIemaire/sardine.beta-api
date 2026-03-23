"""
Schemas Pydantic pour le journal d'audit.
"""

from datetime import datetime

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    """Lecture d'une entree d'audit."""

    id: str
    user_id: str | None
    organization_id: str | None
    action: str
    details: str
    created_at: datetime

    @classmethod
    def from_log(cls, log) -> "AuditLogRead":
        return cls(
            id=str(log.id),
            user_id=log.user_id,
            organization_id=log.organization_id,
            action=log.action,
            details=log.details,
            created_at=log.created_at,
        )
