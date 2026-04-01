"""
Dependencies d'injection FastAPI pour l'authentification.
Supporte deux schémas :
  - Bearer <jwt>   → authentification classique par JWT
  - ApiKey <token>  → authentification par clé API organisationnelle
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.core.enums import Status, UserRole
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.features.auth.models import User
from app.features.auth.service import is_token_blacklisted

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    """
    Résout l'utilisateur courant selon le schéma d'authentification :
    - Authorization: Bearer <jwt> → décode le JWT
    - Authorization: ApiKey <token> → vérifie la clé API
    """
    auth_header = request.headers.get("Authorization", "")

    # ─── Schéma ApiKey ───────────────────────────────────────
    if auth_header.startswith("ApiKey "):
        api_token = auth_header[7:]  # len("ApiKey ") == 7
        if not api_token:
            raise UnauthorizedError("Clé API manquante")

        from app.features.api_keys.service import authenticate_api_key

        result = await authenticate_api_key(api_token)
        if result is None:
            raise UnauthorizedError("Clé API invalide ou révoquée")

        api_key, organization = result

        # Charger l'utilisateur qui a créé la clé
        user = await User.get(api_key.created_by)
        if user is None or user.status != Status.ACTIVE:
            raise UnauthorizedError("Utilisateur associé à la clé API introuvable ou désactivé")

        # Stocker la clé et l'organisation sur la requête pour usage ultérieur
        request.state.api_key = api_key
        request.state.api_key_org = organization
        return user

    # ─── Schéma Bearer (JWT) ─────────────────────────────────
    if not token:
        raise UnauthorizedError("Token manquant")

    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedError("Token invalide ou expiré")

    # Vérification blacklist (déconnexion)
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        raise UnauthorizedError("Token has been revoked")

    user_id = payload.get("sub")
    user = await User.get(PydanticObjectId(user_id))

    if user is None:
        raise UnauthorizedError("Utilisateur non trouvé")
    if user.status != Status.ACTIVE:
        raise UnauthorizedError("Compte désactivé")

    return user


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Vérifie que l'utilisateur courant est administrateur.
    Utilisé comme dependency sur les routes d'administration.
    """
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Accès réservé aux administrateurs")
    return current_user


# Alias typés pour injection dans les routes
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdmin = Annotated[User, Depends(get_current_admin)]
