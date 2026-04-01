"""
Schemas Pydantic pour les clés API.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class ApiKeyRead(BaseModel):
    """Réponse standard (sans le token en clair)."""
    id: str
    name: str
    prefix: str
    status: int
    status_label: str
    created_by: str
    created_at: datetime

    @classmethod
    def from_model(cls, key) -> "ApiKeyRead":
        return cls(
            id=str(key.id),
            name=key.name,
            prefix=key.prefix,
            status=key.status,
            status_label="Active" if key.status == 1 else "Révoquée",
            created_by=str(key.created_by),
            created_at=key.created_at,
        )


class ApiKeyCreated(ApiKeyRead):
    """Réponse de création — inclut le token en clair (une seule fois)."""
    token: str

    @classmethod
    def from_model(cls, key, token: str) -> "ApiKeyCreated":
        return cls(
            id=str(key.id),
            name=key.name,
            prefix=key.prefix,
            status=key.status,
            status_label="Active" if key.status == 1 else "Révoquée",
            created_by=str(key.created_by),
            created_at=key.created_at,
            token=token,
        )
