"""
Configuration centralisée via pydantic-settings.
Les valeurs sont lues depuis le fichier .env à la racine du projet.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # MongoDB
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_NAME: str = "SardineBeta"

    # JWT
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Application
    ENVIRONMENT: str = "development"

    # CORS — liste d'origines autorisées (séparées par virgule en .env)
    # Ex: CORS_ALLOWED_ORIGINS=https://sardine.sendoc.fr,https://app.sardine.sendoc.fr
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    # Purge corbeille : intervalle en heures entre deux exécutions
    TRASH_PURGE_INTERVAL_HOURS: int = 24

    # Serveur GPU pour les nœuds classification / determination / agent
    GPU_API_BASE_URL: str = ""
    GPU_API_KEY: str = ""
    GPU_API_TIMEOUT: int = 120  # secondes

    # Brevo (envoi d'emails transactionnels)
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "noreply@sardine.app"
    BREVO_SENDER_NAME: str = "Sardine"

    HF_TOKEN: str = ""  # Token d'accès Hugging Face pour les modèles privés

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        """Accepte 'a,b,c' depuis .env en plus du format JSON natif."""
        if isinstance(v, str):
            return [s.strip().rstrip("/") for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().rstrip("/") for s in v if str(s).strip()]
        return v


settings = Settings()
