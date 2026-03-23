"""
Routes d'authentification.
Toutes les routes sont publiques sauf /change-password, /logout et /resend-verification.
"""

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.audit import log_action
from app.core.security import create_access_token, create_refresh_token
from app.features.auth.dependencies import CurrentUser
from app.features.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.features.auth.service import (
    authenticate_user,
    change_password,
    logout_token,
    refresh_access_token,
    register_user,
    request_password_reset,
    reset_password,
    send_verification_email,
    verify_email,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest):
    """
    Inscription d'un nouvel utilisateur.
    Cree le compte, l'organisation privee, et retourne access + refresh token.
    """
    user = await register_user(payload)

    # Creation automatique de l'organisation privee
    from app.features.organizations.service import create_private_organization
    await create_private_organization(user)

    # Envoi du mail de verification
    await send_verification_email(user)

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest):
    """
    Connexion. Retourne access + refresh token + id de l'org privee
    pour la redirection post-connexion.
    """
    access_token, refresh_token, user = await authenticate_user(
        payload.email, payload.password,
    )

    # Recuperation de l'org privee pour redirection
    from app.features.organizations.models import Organization
    private_org = await Organization.find_one(
        Organization.owner_id == user.id,
        Organization.is_private == True,  # noqa: E712
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "private_organization_id": str(private_org.id) if private_org else None,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest):
    """
    Renouvelle les tokens a partir d'un refresh token valide.
    L'ancien refresh token est revoque (rotation).
    """
    access_token, new_refresh_token = await refresh_access_token(
        payload.refresh_token,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.post("/change-password", response_model=MessageResponse)
async def change_password_route(
    payload: ChangePasswordRequest,
    current_user: CurrentUser,
):
    """Changement de mot de passe (utilisateur connecte)."""
    await change_password(current_user, payload)
    return MessageResponse(message="Mot de passe modifie avec succes")


@router.post("/logout", response_model=MessageResponse)
async def logout(
    current_user: CurrentUser,
    token: str = Depends(oauth2_scheme),
):
    """Deconnexion — invalide le token JWT en l'ajoutant a la blacklist."""
    await logout_token(token)
    await log_action(current_user.id, "LOGOUT", f"User {current_user.email} logged out")
    return MessageResponse(message="Deconnexion reussie")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    """
    Demande de reinitialisation de mot de passe.
    Retourne toujours un message de succes pour ne pas reveler
    si l'email existe en base (securite).
    """
    await request_password_reset(payload.email)
    return MessageResponse(
        message="Si cet email existe, un lien de reinitialisation a ete envoye."
    )


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password_route(payload: ResetPasswordRequest):
    """Reinitialisation effective du mot de passe avec token."""
    await reset_password(payload)
    return MessageResponse(message="Mot de passe reinitialise avec succes")


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email_route(payload: VerifyEmailRequest):
    """Verification de l'adresse email via le token."""
    await verify_email(payload.token)
    return MessageResponse(message="Email verifie avec succes")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(current_user: CurrentUser):
    """Renvoi du mail de verification."""
    if current_user.email_verified:
        return MessageResponse(message="Email deja verifie")
    await send_verification_email(current_user)
    return MessageResponse(message="Email de verification renvoye")
