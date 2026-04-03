"""
Service d'authentification — logique métier centralisée.
Gère : inscription, connexion (avec brute force), changement/reset
de mot de passe, vérification email, déconnexion (blacklist JWT).
"""

import logging
from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId
from jose import jwt

from app.config import settings
from app.core.audit import log_action
from app.core.enums import Status, UserRole
from app.core.exceptions import (
    ConflictError,
    LockedError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    create_verification_token,
    decode_refresh_token,
    decode_reset_token,
    decode_verification_token,
    hash_password,
    verify_password,
)
from app.core.validators import validate_password
from app.features.auth.models import TokenBlacklist, User
from app.features.auth.schemas import (
    ChangePasswordRequest,
    RegisterRequest,
    ResetPasswordRequest,
)

logger = logging.getLogger(__name__)

# Constantes brute force
MAX_LOGIN_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


async def register_user(payload: RegisterRequest) -> User:
    """
    Crée un compte utilisateur.
    - Valide la politique de mot de passe
    - Vérifie l'unicité de l'email
    - Statut Actif, rôle Utilisateur par défaut
    L'organisation privée est créée par le service organizations (appelé depuis le router).
    """
    validate_password(payload.password)

    existing = await User.find_one(User.email == payload.email)
    if existing:
        raise ConflictError("Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
        role=UserRole.USER,
        status=Status.ACTIVE,
    )
    await user.insert()

    # Génération de la photo de profil (gradient pastel)
    from app.core.avatar import generate_avatar
    avatar_path = generate_avatar(str(user.id))
    await user.set({"avatar_path": avatar_path})

    return user


async def authenticate_user(email: str, password: str) -> tuple[str, User]:
    """
    Connexion avec protection brute force.
    Retourne (access_token, user) si succès.

    Flux brute force :
      1. Si le compte est verrouillé et le délai pas expiré → LockedError
      2. Si les identifiants sont incorrects → incrémente le compteur
      3. Après MAX_LOGIN_ATTEMPTS échecs → verrouille le compte 15 min
      4. Connexion réussie → réinitialise le compteur
    """
    user = await User.find_one(User.email == email)

    if not user:
        # Message générique pour ne pas révéler l'existence de l'email
        raise UnauthorizedError("Invalid email or password")

    # Vérification du verrouillage temporaire
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        raise LockedError()

    # Vérification du mot de passe
    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1

        # Seuil atteint : verrouillage du compte pendant 15 min
        if user.failed_login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=LOCK_DURATION_MINUTES
            )
            await log_action(user.id, "BRUTE_FORCE_LOCK", f"Account locked after {MAX_LOGIN_ATTEMPTS} failed attempts")

        await user.save()
        raise UnauthorizedError("Invalid email or password")

    # Seuls les utilisateurs actifs peuvent se connecter
    if user.status != Status.ACTIVE:
        raise UnauthorizedError("Account is deactivated")

    # Connexion réussie : réinitialisation du compteur brute force
    user.failed_login_attempts = 0
    user.locked_until = None
    await user.save()

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))
    await log_action(user.id, "LOGIN", f"User {user.email} logged in")

    return access_token, refresh_token, user


async def change_password(user: User, payload: ChangePasswordRequest) -> None:
    """
    Changement de mot de passe.
    L'ancien mot de passe doit être vérifié avant modification.
    Le nouveau respecte la politique de complexité.
    """
    if not verify_password(payload.old_password, user.hashed_password):
        raise UnauthorizedError("Ancien mot de passe incorrect")

    validate_password(payload.new_password)

    user.hashed_password = hash_password(payload.new_password)
    user.updated_at = datetime.now(timezone.utc)
    await user.save()


async def logout_token(token: str) -> None:
    """
    Déconnexion — ajoute le JTI du token à la blacklist.
    Le token reste en blacklist jusqu'à son expiration naturelle.
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    jti = payload.get("jti")
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

    blacklisted = TokenBlacklist(jti=jti, expires_at=exp)
    await blacklisted.insert()


async def is_token_blacklisted(jti: str) -> bool:
    """Vérifie si un JTI a été invalidé (déconnexion)."""
    return await TokenBlacklist.find_one(TokenBlacklist.jti == jti) is not None


async def request_password_reset(email: str) -> str | None:
    """
    Génère un token de réinitialisation.
    Retourne le token (en production, il serait envoyé par email).
    Ne révèle pas si l'email existe pour des raisons de sécurité.
    """
    user = await User.find_one(User.email == email)
    if not user:
        # On ne révèle pas l'absence du compte — on retourne silencieusement
        return None

    token = create_reset_token(email)
    # En production : envoyer l'email avec le lien contenant le token
    logger.info("PASSWORD_RESET_TOKEN for %s: %s", email, token)
    return token


async def reset_password(payload: ResetPasswordRequest) -> None:
    """
    Applique la réinitialisation du mot de passe.
    Valide le token, puis met à jour le mot de passe.
    """
    email = decode_reset_token(payload.token)
    if not email:
        raise UnauthorizedError("Token de réinitialisation invalide ou expiré")

    user = await User.find_one(User.email == email)
    if not user:
        raise NotFoundError("Utilisateur non trouvé")

    validate_password(payload.new_password)

    user.hashed_password = hash_password(payload.new_password)
    # Réinitialise aussi le verrouillage brute force si actif
    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    await log_action(user.id, "PASSWORD_RESET", f"Password reset for {email}")


async def send_verification_email(user: User) -> str:
    """
    Génère et "envoie" le token de vérification email.
    En dev, le token est loggé en console.
    """
    token = create_verification_token(user.email)
    logger.info("EMAIL_VERIFICATION_TOKEN for %s: %s", user.email, token)
    return token


async def verify_email(token: str) -> None:
    """Active la vérification email via le token."""
    email = decode_verification_token(token)
    if not email:
        raise UnauthorizedError("Token de vérification invalide ou expiré")

    user = await User.find_one(User.email == email)
    if not user:
        raise NotFoundError("Utilisateur non trouvé")

    user.email_verified = True
    user.updated_at = datetime.now(timezone.utc)
    await user.save()


async def refresh_access_token(refresh_token_str: str) -> tuple[str, str]:
    """
    Genere une nouvelle paire access + refresh token a partir d'un refresh token valide.
    L'ancien refresh token est blackliste (rotation).
    Retourne (new_access_token, new_refresh_token).
    """
    payload = decode_refresh_token(refresh_token_str)
    if payload is None:
        raise UnauthorizedError("Refresh token invalide ou expire")

    # JTI obligatoire pour la revocation
    jti = payload.get("jti")
    if not jti:
        raise UnauthorizedError("Refresh token invalide (JTI manquant)")
    if await is_token_blacklisted(jti):
        raise UnauthorizedError("Refresh token revoque")

    user_id = payload.get("sub")
    user = await User.get(user_id)
    if user is None:
        raise UnauthorizedError("Utilisateur non trouve")
    if user.status != Status.ACTIVE:
        raise UnauthorizedError("Compte desactive")

    # Blacklister l'ancien refresh token (rotation)
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    blacklisted = TokenBlacklist(jti=jti, expires_at=exp)
    try:
        await blacklisted.insert()
    except Exception:
        # DuplicateKeyError = token déjà blacklisté (double appel front)
        pass

    # Generer la nouvelle paire
    new_access = create_access_token(subject=str(user.id))
    new_refresh = create_refresh_token(subject=str(user.id))

    return new_access, new_refresh
