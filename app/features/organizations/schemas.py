"""
Schémas Pydantic pour les organisations.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.core.enums import STATUS_LABELS


class OrganizationCreate(BaseModel):
    """Création d'une organisation collaborative."""
    name: str
    external_reference: str | None = None
    distributor_org_id: str | None = None  # ID de l'organisation distributrice
    parent_org_id: str | None = None       # ID de l'organisation mère


class OrganizationInvite(BaseModel):
    """Invitation d'un utilisateur dans une organisation."""
    user_id: str


class OrganizationUpdate(BaseModel):
    """Modification d'une organisation."""
    name: str | None = None
    contact_email: str | None = None
    external_reference: str | None = None
    distributor_org_id: str | None = None
    parent_org_id: str | None = None
    status: int | None = None  # 0 = Inactif, 1 = Actif


class MemberStatusUpdate(BaseModel):
    """Activation / désactivation d'un membre d'organisation."""
    status: int  # 0 = Inactif, 1 = Actif


class MemberRoleUpdate(BaseModel):
    """Changement de rôle d'un membre d'organisation."""
    role: int  # 1 = Propriétaire (Owner), 2 = Membre


class BulkInviteMember(BaseModel):
    """Un membre à inviter en masse."""
    email: EmailStr
    password: str


class BulkInviteRequest(BaseModel):
    """Invitation en masse de membres dans une organisation."""
    members: list[BulkInviteMember]


class BulkInviteResult(BaseModel):
    """Résultat d'une invitation individuelle."""
    email: str
    status: str  # "created" | "existing" | "error"
    detail: str


class OrganizationRead(BaseModel):
    """Réponse de lecture d'une organisation."""
    id: str
    name: str
    is_private: bool
    status: int
    status_label: str
    contact_email: str | None
    external_reference: str | None
    distributor_org_id: str | None
    parent_org_id: str | None
    owner_id: str
    is_active_member: bool = True  # False = membre désactivé (cadenas côté front)
    created_at: datetime

    @classmethod
    def from_org(cls, org, is_active_member: bool = True) -> "OrganizationRead":
        return cls(
            id=str(org.id),
            name=org.name,
            is_private=org.is_private,
            status=org.status,
            status_label=STATUS_LABELS.get(org.status, "Inconnu"),
            contact_email=org.contact_email,
            external_reference=org.external_reference,
            distributor_org_id=str(org.distributor_org_id) if org.distributor_org_id else None,
            parent_org_id=str(org.parent_org_id) if org.parent_org_id else None,
            owner_id=str(org.owner_id),
            is_active_member=is_active_member,
            created_at=org.created_at,
        )
