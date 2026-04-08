"""Nœud flow (subflow) — exécute un autre flow comme une vraie sous-exécution."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult


MAX_DEPTH = 10

# Registre global des Events de complétion (pour réveiller le parent)
_child_completion_events: dict[str, asyncio.Event] = {}


def _notify_child_completion(execution_id: str) -> None:
    """Appelé par _complete_execution / _fail_execution pour réveiller le parent."""
    event = _child_completion_events.get(execution_id)
    if event:
        event.set()


async def execute_subflow(node: dict, context: ExecutionContext, engine) -> NodeResult:
    from app.features.flows.engine import register_execution
    from app.features.flows.engine.engine import FlowEngine
    from app.features.flows.models import Flow, FlowExecution, FlowVersion

    config = node.get("config", {})
    sub_flow_id = config.get("flowId")

    if not sub_flow_id:
        return NodeResult(error="FLOW: 'flowId' requis dans la config")

    # Profondeur de récursion
    current_depth = context.metadata.get("depth", 0)
    if current_depth >= MAX_DEPTH:
        return NodeResult(error=f"FLOW: profondeur max ({MAX_DEPTH}) atteinte")

    sub_flow = await Flow.get(PydanticObjectId(sub_flow_id))
    if not sub_flow:
        return NodeResult(error=f"FLOW: sous-flow '{sub_flow_id}' introuvable")

    # Charger le flow_data depuis la version active
    if not sub_flow.active_version_id:
        return NodeResult(error=f"FLOW: sous-flow '{sub_flow_id}' n'a pas de version active")
    sub_version = await FlowVersion.get(sub_flow.active_version_id)
    if not sub_version:
        return NodeResult(error="FLOW: version active du sous-flow introuvable")

    org_id = context.metadata.get("org_id")
    triggered_by = context.metadata.get("triggered_by")
    parent_execution_id = context.metadata.get("execution_id")
    parent_flow_id = context.metadata.get("flow_id")

    # Créer une vraie exécution enfant en base
    child_exec = FlowExecution(
        flow_id=sub_flow.id,
        organization_id=PydanticObjectId(org_id) if org_id else sub_flow.organization_id,
        status="pending",
        trigger_type="subflow",
        triggered_by=PydanticObjectId(triggered_by) if triggered_by else None,
        parent_execution_id=parent_execution_id,
        parent_flow_id=parent_flow_id,
        parent_node_id=node["id"],
        execution_data=deepcopy(context.data),
        started_at=datetime.now(UTC),
    )
    await child_exec.insert()
    child_execution_id = str(child_exec.id)

    # Event de complétion
    completion_event = asyncio.Event()
    _child_completion_events[child_execution_id] = completion_event

    # Lancer le child engine en background
    child_engine = FlowEngine()
    child_input = deepcopy(context.data)
    flow_doc = {
        "_id": sub_flow.id,
        "flow_data": sub_version.flow_data,
    }
    child_task = asyncio.create_task(
        child_engine.run(
            flow_doc, child_execution_id,
            str(sub_flow.organization_id), child_input,
            triggered_by, depth=current_depth + 1,
        )
    )
    register_execution(child_execution_id, child_task)

    # Attendre la complétion
    try:
        await completion_event.wait()
    except asyncio.CancelledError:
        from app.features.flows.engine import cancel_execution
        cancel_execution(child_execution_id)
        raise
    finally:
        _child_completion_events.pop(child_execution_id, None)

    # Vérifier le résultat
    child_after = await FlowExecution.get(PydanticObjectId(child_execution_id))
    if not child_after:
        return NodeResult(error="FLOW: exécution enfant introuvable après complétion")

    if child_after.status == "completed":
        context.data = child_after.execution_data or context.data
        return NodeResult(
            output_port=0,
            metadata={
                "child_execution_id": child_execution_id,
                "child_flow_id": sub_flow_id,
                "child_flow_name": sub_flow.name,
                "depth": current_depth + 1,
            },
        )
    elif child_after.status == "failed":
        return NodeResult(error=f"Sous-flow échoué: {child_after.error or 'Erreur inconnue'}")
    else:
        return NodeResult(error=f"Sous-flow terminé avec statut inattendu: {child_after.status}")
