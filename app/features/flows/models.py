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

    # Suppression douce — None = actif, datetime = en corbeille
    deleted_at: datetime | None = None

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


# ─── Runtime : exécution d'un flow ───────────────────────────────


class FlowExecution(Document):
    """
    Exécution d'un flow. Enregistre l'état complet + métadonnées.
    Statut : pending, running, completed, failed, waiting, cancelled
    """

    flow_id: Indexed(PydanticObjectId)
    organization_id: Indexed(PydanticObjectId)
    status: str = Field(default="pending")  # pending, running, completed, failed, waiting, cancelled

    trigger_type: str = "manual"  # manual, scheduled, webhook, subflow
    triggered_by: PydanticObjectId | None = None

    # Timeline
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Output / erreur
    execution_data: dict = Field(default_factory=dict)
    error: str | None = None

    # Pause / approval
    paused_at_node: str | None = None
    paused_node_log_id: str | None = None
    context_snapshot: dict | None = None

    # Sous-flow
    parent_execution_id: str | None = None
    parent_flow_id: str | None = None
    parent_node_id: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "flow_executions"


class ExecutionNodeLog(Document):
    """
    Journal d'exécution d'un nœud individuel.
    Permet de tracer step-by-step et de reprendre après pause.
    """

    execution_id: Indexed(PydanticObjectId)
    node_id: str
    node_type: str
    node_name: str
    status: str = "running"  # running, completed, failed, waiting

    output_port: int | None = None
    error: str | None = None
    metadata: dict | None = None

    input_data: dict | None = None
    output_data: dict | None = None

    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # Loop tracking
    parent_node_id: str | None = None
    loop_iteration: int | None = None
    loop_total: int | None = None

    class Settings:
        name = "execution_node_logs"


class ApprovalTask(Document):
    """
    Tâche d'approbation créée par un nœud `approval` lors d'une pause.
    L'utilisateur cible répond via /approval-tasks/{id}/respond.
    """

    flow_id: PydanticObjectId
    execution_id: Indexed(PydanticObjectId)
    node_id: str
    organization_id: PydanticObjectId

    title: str
    message: str
    options: list[dict] = Field(default_factory=list)  # [{"label": str, "value": str}]

    assignee_type: str = "user"  # user, team
    assignee_id: PydanticObjectId | None = None  # user_id ou team_id

    status: str = "pending"  # pending, responded, expired
    response: str | None = None
    response_label: str | None = None
    responded_by: PydanticObjectId | None = None
    responded_at: datetime | None = None

    expires_at: datetime | None = None
    timeout_action: str = "reject"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "approval_tasks"
