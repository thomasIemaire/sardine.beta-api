"""
Modèle ApiKey.
Stocke les clés API associées à une organisation.
La clé complète n'est jamais stockée en clair : seul le préfixe (12 premiers
caractères) est conservé pour identification visuelle, et le hash SHA-256
du token complet est utilisé pour l'authentification.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class ApiKey(Document):
    organization_id: Indexed(PydanticObjectId)
    name: str  # Nom donné par l'utilisateur (ex: "Production")
    prefix: str  # 12 premiers caractères de la clé (ex: "srd_a3f8c2e1")
    hashed_key: str  # SHA-256 du token complet, jamais exposé
    status: int = 1  # 1 = active, 0 = revoked
    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "api_keys"
