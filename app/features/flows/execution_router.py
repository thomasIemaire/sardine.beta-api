"""
Routes d'exécution des flows : démarrage, suivi, approval tasks, logs.
"""

from fastapi import APIRouter, Query

from app.features.auth.dependencies import CurrentUser
from app.features.flows.execution_service import (
    execute_flow,
    get_execution,
    get_node_log,
    list_approval_tasks,
    list_executions,
    list_node_logs,
    respond_approval,
    stop_execution,
)
from app.features.flows.schemas import (
    ApprovalRespondRequest,
    ApprovalTaskRead,
    FlowExecuteRequest,
    FlowExecutionRead,
    NodeLogRead,
)

router = APIRouter(
    prefix="/organizations/{org_id}",
    tags=["Flow Executions"],
)


# ─── Démarrer une exécution ─────────────────────────────────────


@router.post(
    "/flows/{flow_id}/execute",
    response_model=FlowExecutionRead,
    status_code=201,
)
async def execute(
    org_id: str, flow_id: str,
    payload: FlowExecuteRequest, current_user: CurrentUser,
):
    """Démarre l'exécution d'un flow en arrière-plan."""
    execution = await execute_flow(
        current_user, org_id, flow_id, input_data=payload.input_data,
    )
    return FlowExecutionRead.from_execution(execution)


# ─── Lister / consulter les exécutions ──────────────────────────


@router.get("/flows/{flow_id}/executions")
async def list_flow_executions(
    org_id: str, flow_id: str, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Liste paginée des exécutions d'un flow."""
    result = await list_executions(
        current_user, org_id, flow_id=flow_id, page=page, page_size=page_size,
    )
    return {
        "items": [FlowExecutionRead.from_execution(e) for e in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get(
    "/flows/{flow_id}/executions/{execution_id}",
    response_model=FlowExecutionRead,
)
async def get_flow_execution(
    org_id: str, flow_id: str, execution_id: str, current_user: CurrentUser,
):
    """Détail d'une exécution."""
    execution = await get_execution(current_user, org_id, execution_id)
    return FlowExecutionRead.from_execution(execution)


@router.post(
    "/flows/{flow_id}/executions/{execution_id}/stop",
    response_model=FlowExecutionRead,
)
async def stop_flow_execution(
    org_id: str, flow_id: str, execution_id: str, current_user: CurrentUser,
):
    """Annule une exécution en cours."""
    execution = await stop_execution(current_user, org_id, execution_id)
    return FlowExecutionRead.from_execution(execution)


# ─── Logs des nœuds ─────────────────────────────────────────────


@router.get(
    "/flows/{flow_id}/executions/{execution_id}/nodes",
    response_model=list[NodeLogRead],
)
async def list_execution_node_logs(
    org_id: str, flow_id: str, execution_id: str, current_user: CurrentUser,
):
    """Liste tous les logs de nœuds d'une exécution."""
    logs = await list_node_logs(current_user, org_id, execution_id)
    return [NodeLogRead.from_log(log) for log in logs]


@router.get(
    "/flows/{flow_id}/executions/{execution_id}/nodes/{node_log_id}",
    response_model=NodeLogRead,
)
async def get_execution_node_log(
    org_id: str, flow_id: str, execution_id: str,
    node_log_id: str, current_user: CurrentUser,
):
    """Détail d'un log de nœud (input/output complets)."""
    log = await get_node_log(current_user, org_id, execution_id, node_log_id)
    return NodeLogRead.from_log(log)


# ─── Approval tasks ─────────────────────────────────────────────


@router.get("/approval-tasks", response_model=list[ApprovalTaskRead])
async def list_my_approval_tasks(
    org_id: str, current_user: CurrentUser,
    status: str | None = Query(None, description="pending | responded | expired"),
):
    """Liste les approbations adressées au user courant."""
    tasks = await list_approval_tasks(current_user, org_id, status=status)
    return [ApprovalTaskRead.from_task(t) for t in tasks]


@router.post(
    "/approval-tasks/{task_id}/respond",
    response_model=ApprovalTaskRead,
)
async def respond_to_approval(
    org_id: str, task_id: str,
    payload: ApprovalRespondRequest, current_user: CurrentUser,
):
    """
    Répond à une tâche d'approbation. La valeur doit correspondre à
    l'une des "value" des options de la tâche. La reprise du flow
    est lancée automatiquement.
    """
    task = await respond_approval(current_user, org_id, task_id, payload.response)
    return ApprovalTaskRead.from_task(task)
