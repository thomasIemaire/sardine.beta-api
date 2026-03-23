"""
Modèle User — document principal d'authentification.
Gère aussi la protection brute force via failed_login_attempts / locked_until.
"""

from datetime import datetime, timezone

from beanie import Document, Indexed
from pydantic import EmailStr, Field

from app.core.enums import Status, UserRole


class User(Document):
    email: Indexed(EmailStr, unique=True)
    hashed_password: str
    first_name: str
    last_name: str

    # Rôle : 0 = Admin, 1 = Utilisateur (défaut Utilisateur)
    role: int = Field(default=UserRole.USER)

    # Statut : 1 = Actif, 0 = Inactif (défaut Actif)
    status: int = Field(default=Status.ACTIVE)

    # Photo de profil (chemin relatif vers le fichier généré)
    avatar_path: str | None = None

    # Vérification email
    email_verified: bool = False

    # Protection brute force : après 5 échecs → verrouillage 15 min
    failed_login_attempts: int = 0
    locked_until: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "users"


class TokenBlacklist(Document):
    """
    Blacklist des JTI de tokens invalidés (déconnexion).
    Le champ expires_at permet un nettoyage automatique via TTL index MongoDB.
    """
    jti: Indexed(str, unique=True)
    expires_at: datetime

    class Settings:
        name = "token_blacklist"
