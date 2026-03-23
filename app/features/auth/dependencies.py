"""
Dependencies d'injection FastAPI pour l'authentification.
get_current_user décode le JWT, vérifie la blacklist, et charge l'utilisateur courant.
get_current_admin ajoute une vérification de rôle Admin.
"""

from typing import Annotated

from beanie import PydanticObjectId
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.enums import Status, UserRole
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.features.auth.models import User
from app.features.auth.service import is_token_blacklisted

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """
    Résout le token JWT en objet User.
    Vérifie : validité du token, blacklist, existence et statut actif.
    """
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
