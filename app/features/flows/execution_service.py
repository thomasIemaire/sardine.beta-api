"""
Service d'exécution des flows.
- execute_flow : démarre une exécution en arrière-plan
- resume_approval : répond à une approbation et reprend l'exécution
- list_executions / get_execution : consultation
- list_node_logs : journal d'exécution par nœud
"""

import asyncio
from datetime import UTC, datetime

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.membership import check_org_membership
from app.core.pagination import paginate
from app.features.auth.models import User
from app.features.flows.engine import FlowEngine, cancel_execution, register_execution
from app.features.flows.models import (
    ApprovalTask,
    ExecutionNodeLog,
    Flow,
    FlowExecution,
    FlowVersion,
)


# ─── Démarrer une exécution ─────────────────────────────────────


async def execute_flow(
    user: User,
    org_id: str,
    flow_id: str,
    input_data: dict | None = None,
    trigger_type: str = "manual",
) -> FlowExecution:
    """
    Démarre l'exécution d'un flow en arrière-plan.
    Retourne le document d'exécution avec status="pending".
    Le moteur passe ensuite à "running" puis "completed"/"failed"/"waiting".
    """
    await check_org_membership(user, org_id)

    flow = await Flow.get(PydanticObjectId(flow_id))
    if not flow or str(flow.organization_id) != org_id:
        raise NotFoundError("Flow non trouvé dans cette organisation")

    if not flow.active_version_id:
        raise ValidationError("Le flow n'a pas de version active")

    version = await FlowVersion.get(flow.active_version_id)
    if not version:
        raise ValidationError("Version active introuvable")

    # Créer l'exécution
    execution = FlowExecution(
        flow_id=flow.id,
        organization_id=PydanticObjectId(org_id),
        status="pending",
        trigger_type=trigger_type,
        triggered_by=user.id,
        execution_data=input_data or {},
        started_at=datetime.now(UTC),
    )
    await execution.insert()

    # Lancer le moteur en background
    engine = FlowEngine()
    flow_doc = {"_id": flow.id, "flow_data": version.flow_data}
    task = asyncio.create_task(
        engine.run(flow_doc, str(execution.id), org_id, input_data, str(user.id))
    )
    register_execution(str(execution.id), task)

    await log_action(
        user_id=user.id,
        action="FLOW_EXECUTE",
        details=f"Exécution du flow « {flow.name} » démarrée",
        organization_id=flow.organization_id,
    )

    return execution


# ─── Annuler une exécution ──────────────────────────────────────


async def stop_execution(user: User, org_id: str, execution_id: str) -> FlowExecution:
    await check_org_membership(user, org_id)

    execution = await FlowExecution.get(PydanticObjectId(execution_id))
    if not execution or str(execution.organization_id) != org_id:
        raise NotFoundError("Exécution non trouvée")

    if execution.status not in ("pending", "running", "waiting"):
        raise ConflictError("L'exécution n'est pas en cours")

    cancel_execution(execution_id)
    await execution.set({
        "status": "cancelled",
        "completed_at": datetime.now(UTC),
    })

    await log_action(
        user_id=user.id,
        action="FLOW_STOP",
        details=f"Exécution {execution_id} annulée",
        organization_id=execution.organization_id,
    )

    return execution


# ─── Lister / consulter les exécutions ──────────────────────────


async def list_executions(
    user: User, org_id: str, flow_id: str | None = None,
    page: int = 1, page_size: int = 20,
):
    await check_org_membership(user, org_id)

    filters: dict = {"organization_id": PydanticObjectId(org_id)}
    if flow_id:
        filters["flow_id"] = PydanticObjectId(flow_id)

    query = FlowExecution.find(filters)
    return await paginate(query, page, page_size, sort_field="-created_at")


async def get_execution(user: User, org_id: str, execution_id: str) -> FlowExecution:
    await check_org_membership(user, org_id)

    execution = await FlowExecution.get(PydanticObjectId(execution_id))
    if not execution or str(execution.organization_id) != org_id:
        raise NotFoundError("Exécution non trouvée")

    return execution


# ─── Logs des nœuds ─────────────────────────────────────────────


async def list_node_logs(
    user: User, org_id: str, execution_id: str,
) -> list[ExecutionNodeLog]:
    """Liste tous les logs de nœuds pour une exécution donnée."""
    await check_org_membership(user, org_id)

    execution = await FlowExecution.get(PydanticObjectId(execution_id))
    if not execution or str(execution.organization_id) != org_id:
        raise NotFoundError("Exécution non trouvée")

    return await ExecutionNodeLog.find(
        ExecutionNodeLog.execution_id == execution.id,
    ).sort("+started_at").to_list()


async def get_node_log(
    user: User, org_id: str, execution_id: str, node_log_id: str,
) -> ExecutionNodeLog:
    """Récupère le détail d'un log de nœud (input/output complets)."""
    await check_org_membership(user, org_id)

    execution = await FlowExecution.get(PydanticObjectId(execution_id))
    if not execution or str(execution.organization_id) != org_id:
        raise NotFoundError("Exécution non trouvée")

    log = await ExecutionNodeLog.get(PydanticObjectId(node_log_id))
    if not log or log.execution_id != execution.id:
        raise NotFoundError("Log de nœud non trouvé")

    return log


# ─── Approval tasks ─────────────────────────────────────────────


async def respond_approval(
    user: User, org_id: str, task_id: str, response_value: str,
) -> ApprovalTask:
    """
    Répond à une tâche d'approbation et reprend l'exécution du flow.
    Seul l'assignee peut répondre.
    """
    await check_org_membership(user, org_id)

    task = await ApprovalTask.get(PydanticObjectId(task_id))
    if not task or str(task.organization_id) != org_id:
        raise NotFoundError("Tâche d'approbation non trouvée")

    if task.status != "pending":
        raise ConflictError("Cette tâche a déjà été traitée")

    # Vérifier que le user est l'assignee
    if task.assignee_type == "user":
        if not task.assignee_id or str(task.assignee_id) != str(user.id):
            raise ForbiddenError("Cette approbation ne vous est pas adressée")

    # Vérifier que la valeur est dans les options
    valid_values = {opt.get("value") for opt in task.options}
    if response_value not in valid_values:
        raise ValidationError(
            f"Valeur invalide : {response_value} (attendu parmi {valid_values})"
        )

    # Récupérer le label
    response_label = response_value
    for opt in task.options:
        if opt.get("value") == response_value:
            response_label = opt.get("label", response_value)
            break

    await task.set({
        "status": "responded",
        "response": response_value,
        "response_label": response_label,
        "responded_by": user.id,
        "responded_at": datetime.now(UTC),
    })

    # Reprendre l'exécution
    engine = FlowEngine()
    asyncio.create_task(engine.resume(str(task.execution_id), response_value))

    await log_action(
        user_id=user.id,
        action="FLOW_APPROVAL_RESPOND",
        details=f"Approbation {task_id} → {response_label}",
        organization_id=task.organization_id,
    )

    return task


async def list_approval_tasks(
    user: User, org_id: str, status: str | None = None,
) -> list[ApprovalTask]:
    """Liste les approval tasks adressées au user courant."""
    await check_org_membership(user, org_id)

    filters: dict = {
        "organization_id": PydanticObjectId(org_id),
        "assignee_id": user.id,
    }
    if status:
        filters["status"] = status

    return await ApprovalTask.find(filters).sort("-created_at").to_list()
