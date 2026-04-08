"""
Configuration centralisée via pydantic-settings.
Les valeurs sont lues depuis le fichier .env à la racine du projet.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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

    # Purge corbeille : intervalle en heures entre deux exécutions
    TRASH_PURGE_INTERVAL_HOURS: int = 24

    # Serveur GPU pour les nœuds classification / determination / agent
    GPU_API_BASE_URL: str = ""
    GPU_API_KEY: str = ""
    GPU_API_TIMEOUT: int = 120  # secondes


settings = Settings()
