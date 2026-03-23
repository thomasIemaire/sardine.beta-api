"""
Utilitaires de sécurité : hashing de mots de passe et gestion des JWT.
Chaque token contient un JTI (JWT ID) unique pour permettre
l'invalidation unitaire lors de la déconnexion.
"""

import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt est le seul schéma actif ; les anciens hashs seraient auto-migrés
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """
    Génère un JWT signé HS256.
    Le claim 'sub' contient l'id utilisateur, 'jti' un UUID unique
    pour la gestion de la blacklist (déconnexion).
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": subject,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """
    Décode et valide un JWT. Retourne le payload complet (sub, jti, exp)
    ou None si le token est invalide/expiré.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("sub") is None:
            return None
        return payload
    except JWTError:
        return None


def create_refresh_token(subject: str) -> str:
    """
    Genere un refresh token JWT longue duree (7 jours par defaut).
    Contient un JTI unique pour permettre la revocation.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
    )
    payload = {
        "sub": subject,
        "exp": expire,
        "jti": str(uuid.uuid4()),
        "purpose": "refresh",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_refresh_token(token: str) -> dict | None:
    """Decode un refresh token. Retourne le payload ou None."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
        )
        if payload.get("purpose") != "refresh":
            return None
        if payload.get("sub") is None:
            return None
        return payload
    except JWTError:
        return None


def create_reset_token(email: str) -> str:
    """
    Token de réinitialisation de mot de passe.
    Durée de vie courte : 15 minutes.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    return jwt.encode(
        {"sub": email, "exp": expire, "purpose": "password_reset"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_reset_token(token: str) -> str | None:
    """Décode un token de reset. Retourne l'email ou None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "password_reset":
            return None
        return payload.get("sub")
    except JWTError:
        return None


def create_verification_token(email: str) -> str:
    """Token de vérification d'email. Valide 24h."""
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    return jwt.encode(
        {"sub": email, "exp": expire, "purpose": "email_verification"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_verification_token(token: str) -> str | None:
    """Décode un token de vérification email. Retourne l'email ou None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "email_verification":
            return None
        return payload.get("sub")
    except JWTError:
        return None
