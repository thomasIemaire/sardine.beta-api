"""
Modèle Server.
Décrit un serveur externe (GPU RunPod, GPU local, worker CPU…) dont on
récupère les statistiques live via un endpoint /stats.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed
from pydantic import Field

from app.core.enums import ServerType


class Server(Document):
    name: Indexed(str, unique=True)
    type: ServerType
    base_url: str  # Sans slash final (ex: "https://xxx.proxy.runpod.net")
    api_key: str = ""  # Header x-api-key envoyé au serveur (vide = pas d'auth)
    enabled: bool = True
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "servers"
