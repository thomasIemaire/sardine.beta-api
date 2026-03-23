"""
Schémas Pydantic pour l'authentification.
Chaque schéma correspond à un contrat d'entrée/sortie d'endpoint.
"""

from pydantic import BaseModel, EmailStr, model_validator


class RegisterRequest(BaseModel):
    """Inscription avec confirmation du mot de passe."""
    email: EmailStr
    password: str
    confirm_password: str
    first_name: str
    last_name: str

    # Vérifie que password et confirm_password sont identiques
    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return self


class LoginRequest(BaseModel):
    """Connexion par email + mot de passe."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Reponse contenant access + refresh token."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Requete de renouvellement de token."""
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    """Changement de mot de passe (utilisateur connecté)."""
    old_password: str
    new_password: str
    confirm_new_password: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_new_password:
            raise ValueError("Les nouveaux mots de passe ne correspondent pas.")
        return self


class ForgotPasswordRequest(BaseModel):
    """Demande de réinitialisation par email."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Réinitialisation effective avec le token reçu par email."""
    token: str
    new_password: str
    confirm_new_password: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_new_password:
            raise ValueError("Les mots de passe ne correspondent pas.")
        return self


class VerifyEmailRequest(BaseModel):
    """Vérification de l'email via token."""
    token: str


class MessageResponse(BaseModel):
    """Réponse générique pour les actions sans payload de données."""
    message: str
