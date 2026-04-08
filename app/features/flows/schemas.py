"""
Schémas Pydantic pour les flows, versions et partage.
"""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import FLOW_STATUS_LABELS

# ─── Requêtes ────────────────────────────────────────────────────

class FlowCreate(BaseModel):
    """Création d'un flow avec ses données initiales (1ère version)."""

    name: str
    description: str = ""
    flow_data: dict  # Données JSON de la version initiale


class FlowUpdate(BaseModel):
    """Modification du nom, de la description et/ou du statut d'un flow."""

    name: str | None = None
    description: str | None = None
    status: str | None = None


class FlowVersionCreate(BaseModel):
    """
    Création d'une nouvelle version.
    Si parent_version_id est omis, branche depuis la version active.
    """

    flow_data: dict
    parent_version_id: str | None = None


class ActiveVersionUpdate(BaseModel):
    """Changement de la version active (checkout)."""

    version_id: str


class FlowShareCreate(BaseModel):
    """Partage d'un flow avec une ou plusieurs organisations (lecture seule)."""

    target_org_ids: list[str]


# ─── Exécution ───────────────────────────────────────────────────


class FlowExecuteRequest(BaseModel):
    """Démarrage d'une exécution avec input data optionnelle."""
    input_data: dict | None = None


class ApprovalRespondRequest(BaseModel):
    """Réponse à une tâche d'approbation."""
    response: str  # une des "value" des options


class FlowExecutionRead(BaseModel):
    """Lecture d'une exécution."""
    id: str
    flow_id: str
    organization_id: str
    status: str
    trigger_type: str
    triggered_by: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    execution_data: dict
    paused_at_node: str | None
    parent_execution_id: str | None
    created_at: datetime

    @classmethod
    def from_execution(cls, execution) -> "FlowExecutionRead":
        return cls(
            id=str(execution.id),
            flow_id=str(execution.flow_id),
            organization_id=str(execution.organization_id),
            status=execution.status,
            trigger_type=execution.trigger_type,
            triggered_by=str(execution.triggered_by) if execution.triggered_by else None,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            error=execution.error,
            execution_data=execution.execution_data or {},
            paused_at_node=execution.paused_at_node,
            parent_execution_id=execution.parent_execution_id,
            created_at=execution.created_at,
        )


class NodeLogRead(BaseModel):
    """Lecture d'un log de nœud."""
    id: str
    execution_id: str
    node_id: str
    node_type: str
    node_name: str
    status: str
    output_port: int | None
    error: str | None
    metadata: dict | None
    input_data: dict | None
    output_data: dict | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    parent_node_id: str | None
    loop_iteration: int | None
    loop_total: int | None

    @classmethod
    def from_log(cls, log) -> "NodeLogRead":
        return cls(
            id=str(log.id),
            execution_id=str(log.execution_id),
            node_id=log.node_id,
            node_type=log.node_type,
            node_name=log.node_name,
            status=log.status,
            output_port=log.output_port,
            error=log.error,
            metadata=log.metadata,
            input_data=log.input_data,
            output_data=log.output_data,
            started_at=log.started_at,
            completed_at=log.completed_at,
            duration_ms=log.duration_ms,
            parent_node_id=log.parent_node_id,
            loop_iteration=log.loop_iteration,
            loop_total=log.loop_total,
        )


class ApprovalTaskRead(BaseModel):
    """Lecture d'une tâche d'approbation."""
    id: str
    flow_id: str
    execution_id: str
    node_id: str
    organization_id: str
    title: str
    message: str
    options: list[dict]
    assignee_type: str
    assignee_id: str | None
    status: str
    response: str | None
    response_label: str | None
    responded_by: str | None
    responded_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    @classmethod
    def from_task(cls, task) -> "ApprovalTaskRead":
        return cls(
            id=str(task.id),
            flow_id=str(task.flow_id),
            execution_id=str(task.execution_id),
            node_id=task.node_id,
            organization_id=str(task.organization_id),
            title=task.title,
            message=task.message,
            options=task.options,
            assignee_type=task.assignee_type,
            assignee_id=str(task.assignee_id) if task.assignee_id else None,
            status=task.status,
            response=task.response,
            response_label=task.response_label,
            responded_by=str(task.responded_by) if task.responded_by else None,
            responded_at=task.responded_at,
            expires_at=task.expires_at,
            created_at=task.created_at,
        )


# ─── Réponses ────────────────────────────────────────────────────

class FlowVersionRead(BaseModel):
    """Lecture d'une version de flow."""

    id: str
    flow_id: str
    flow_data: dict
    parent_version_id: str | None
    created_by: str
    created_by_name: str | None = None
    created_at: datetime

    @classmethod
    def from_version(
        cls, version, creator_name: str | None = None,
    ) -> "FlowVersionRead":
        return cls(
            id=str(version.id),
            flow_id=str(version.flow_id),
            flow_data=version.flow_data,
            parent_version_id=(
                str(version.parent_version_id) if version.parent_version_id else None
            ),
            created_by=str(version.created_by),
            created_by_name=creator_name,
            created_at=version.created_at,
        )


class FlowRead(BaseModel):
    """Lecture d'un flow avec optionnellement les données de la version active."""

    id: str
    name: str
    description: str
    organization_id: str
    status: str
    status_label: str
    active_version_id: str | None
    active_version_data: dict | None = None
    forked_from_id: str | None = None
    forked_from_version_id: str | None = None
    created_by: str
    created_by_name: str | None = None
    created_at: datetime

    @classmethod
    def from_flow(
        cls, flow, active_data: dict | None = None,
        creator_name: str | None = None,
    ) -> "FlowRead":
        return cls(
            id=str(flow.id),
            name=flow.name,
            description=flow.description,
            organization_id=str(flow.organization_id),
            status=flow.status,
            status_label=FLOW_STATUS_LABELS.get(flow.status, flow.status),
            active_version_id=(
                str(flow.active_version_id) if flow.active_version_id else None
            ),
            active_version_data=active_data,
            forked_from_id=(
                str(flow.forked_from_id) if flow.forked_from_id else None
            ),
            forked_from_version_id=(
                str(flow.forked_from_version_id)
                if flow.forked_from_version_id else None
            ),
            created_by=str(flow.created_by),
            created_by_name=creator_name,
            created_at=flow.created_at,
        )


class FlowShareRead(BaseModel):
    """Lecture d'un partage de flow."""

    id: str
    flow_id: str
    shared_with_org_id: str
    shared_by: str
    created_at: datetime

    @classmethod
    def from_share(cls, share) -> "FlowShareRead":
        return cls(
            id=str(share.id),
            flow_id=str(share.flow_id),
            shared_with_org_id=str(share.shared_with_org_id),
            shared_by=str(share.shared_by),
            created_at=share.created_at,
        )
