"""
Modèles pour les agents, leur versioning et le partage.

Architecture :
  - Agent : entité principale rattachée à une organisation.
    Pointe vers sa version active via active_version_id (HEAD).
    Peut être un fork d'un autre agent (forked_from_id).
  - AgentVersion : version d'un agent contenant le schéma JSON.
    Forme un arbre (DAG) via parent_version_id, permettant
    le branchement comme dans un système de contrôle de version.
  - AgentShare : relation de partage en lecture seule
    entre un agent et une organisation destinataire.
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class Agent(Document):
    """Agent rattaché à une organisation avec versioning de son schéma."""

    name: str
    description: str = ""
    organization_id: Indexed(PydanticObjectId)

    # HEAD — pointe vers la version active courante
    active_version_id: PydanticObjectId | None = None

    # Fork — référence vers l'agent d'origine et la version forkée
    forked_from_id: PydanticObjectId | None = None
    forked_from_version_id: PydanticObjectId | None = None

    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Suppression douce — None = actif, datetime = en corbeille
    deleted_at: datetime | None = None

    class Settings:
        name = "agents"


class AgentVersion(Document):
    """
    Version d'un agent contenant le schéma JSON.
    parent_version_id forme un arbre permettant le branchement :
    - None = version initiale (racine de l'arbre)
    - ObjectId = branche depuis cette version parente
    """

    agent_id: Indexed(PydanticObjectId)
    schema_data: dict  # Schéma JSON libre
    parent_version_id: PydanticObjectId | None = None

    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "agent_versions"


class AgentFieldFeedback(Document):
    """
    Feedback utilisateur sur un champ extrait par un agent.
    Alimente les statistiques de précision par agent et par champ.
    """

    agent_id: Indexed(PydanticObjectId)
    organization_id: Indexed(PydanticObjectId)

    # Source du feedback
    file_id: PydanticObjectId  # fichier dont vient le champ évalué
    field_key: str             # chemin pointé du champ (ex: "seller.name")
    field_value: str | None    # valeur extraite au moment du feedback (snapshot)

    is_correct: bool           # true = bonne valeur, false = mauvaise valeur

    rated_by: PydanticObjectId
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "agent_field_feedbacks"


class AgentShare(Document):
    """
    Partage d'un agent en lecture seule avec une organisation.
    L'org propriétaire partage, l'org destinataire peut lire.
    """

    agent_id: Indexed(PydanticObjectId)
    shared_with_org_id: Indexed(PydanticObjectId)
    shared_by: PydanticObjectId  # Utilisateur qui a partagé
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "agent_shares"
