"""
Modèles pour les flows, leur versioning et le partage.

Architecture :
  - Flow : entité principale rattachée à une organisation.
    Pointe vers sa version active via active_version_id (HEAD).
    Possède un statut (active, error, pending).
    Peut être un fork d'un autre flow (forked_from_id).
  - FlowVersion : version d'un flow contenant le flow_data (objet JSON).
    Forme un arbre (DAG) via parent_version_id, permettant
    le branchement comme dans un système de contrôle de version.
  - FlowShare : relation de partage en lecture seule
    entre un flow et une organisation destinataire.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.core.enums import FlowStatus


class Flow(Document):
    """Flow rattaché à une organisation avec versioning de ses données."""

    name: str
    description: str = ""
    organization_id: Indexed(PydanticObjectId)

    status: str = Field(default=FlowStatus.PENDING)

    # HEAD — pointe vers la version active courante
    active_version_id: PydanticObjectId | None = None

    # Fork — référence vers le flow d'origine et la version forkée
    forked_from_id: PydanticObjectId | None = None
    forked_from_version_id: PydanticObjectId | None = None

    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "flows"


class FlowVersion(Document):
    """
    Version d'un flow contenant le flow_data (objet JSON).
    parent_version_id forme un arbre permettant le branchement :
    - None = version initiale (racine de l'arbre)
    - ObjectId = branche depuis cette version parente
    """

    flow_id: Indexed(PydanticObjectId)
    flow_data: dict  # Données JSON libres du flow
    parent_version_id: PydanticObjectId | None = None

    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "flow_versions"


class FlowShare(Document):
    """
    Partage d'un flow en lecture seule avec une organisation.
    L'org propriétaire partage, l'org destinataire peut lire.
    """

    flow_id: Indexed(PydanticObjectId)
    shared_with_org_id: Indexed(PydanticObjectId)
    shared_by: PydanticObjectId  # Utilisateur qui a partagé
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "flow_shares"
