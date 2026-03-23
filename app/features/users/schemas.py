"""
Schémas Pydantic pour la gestion des utilisateurs.
Séparés en lecture profil, modification profil, et administration.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.core.enums import STATUS_LABELS, USER_ROLE_LABELS


class UserRead(BaseModel):
    """
    Consultation du profil.
    Inclut les libellés lisibles pour rôle et statut.
    """
    id: str
    email: EmailStr
    first_name: str
    last_name: str
    role: int
    role_label: str  # Libellé lisible : "Administrateur" ou "Utilisateur"
    status: int
    status_label: str  # Libellé lisible : "Actif" ou "Inactif"
    email_verified: bool
    created_at: datetime

    @classmethod
    def from_user(cls, user) -> "UserRead":
        """Factory depuis un document User pour calculer les labels."""
        return cls(
            id=str(user.id),
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=user.role,
            role_label=USER_ROLE_LABELS.get(user.role, "Inconnu"),
            status=user.status,
            status_label=STATUS_LABELS.get(user.status, "Inconnu"),
            email_verified=user.email_verified,
            created_at=user.created_at,
        )


class UserUpdate(BaseModel):
    """Modification du profil (nom, prénom, email)."""
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None


class AdminUserUpdate(BaseModel):
    """Modification admin (statut et/ou rôle)."""
    status: int | None = None  # 0 = Inactif, 1 = Actif
    role: int | None = None    # 0 = Admin, 1 = Utilisateur


class UserListParams(BaseModel):
    """
    Paramètres de filtrage et pagination pour la liste admin.
    Tous les champs sont optionnels.
    """
    search: str | None = None      # Recherche par nom ou email
    status: int | None = None      # Filtre par statut
    role: int | None = None        # Filtre par rôle
    page: int = 1
    page_size: int = 20


# ─── Création massive (admin) ─────────────────────────────────────

class BulkUserItem(BaseModel):
    """
    Un utilisateur à créer dans le payload de création massive.
    organization_ids : liste des IDs d'organisations auxquelles
    l'utilisateur sera ajouté comme membre de l'équipe racine.
    """
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    organization_ids: list[str] = []


class BulkCreateRequest(BaseModel):
    """Payload de création massive d'utilisateurs par un admin."""
    users: list[BulkUserItem]


class BulkCreateResultItem(BaseModel):
    """Résultat de création pour un utilisateur individuel."""
    email: str
    success: bool
    user_id: str | None = None
    error: str | None = None


class BulkCreateResponse(BaseModel):
    """Réponse globale de la création massive."""
    total: int
    created: int
    failed: int
    results: list[BulkCreateResultItem]
