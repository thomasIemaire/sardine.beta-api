"""Nœud approval — met le flow en pause et attend une réponse humaine."""

from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult
from ..expressions import resolve_template


async def execute_approval(node: dict, context: ExecutionContext, engine) -> NodeResult:
    from app.features.auth.models import User
    from app.features.flows.models import ApprovalTask
    from app.features.notifications.service import create_action_notification

    config = node.get("config", {})
    title = resolve_template(config.get("title", "Approbation requise"), context)
    message = resolve_template(config.get("message", ""), context)
    options = config.get("options", [
        {"label": "Approuver", "value": "approved"},
        {"label": "Rejeter", "value": "rejected"},
    ])
    assignee_type = config.get("assigneeType", "user")
    assignee_value = config.get("assigneeId", "")  # email ou user_id
    timeout_minutes = config.get("timeout")
    timeout_action = config.get("timeoutAction", "reject")

    org_id = context.metadata.get("org_id")
    flow_id = context.metadata.get("flow_id")
    execution_id = context.metadata.get("execution_id")

    if not org_id or not flow_id or not execution_id:
        return NodeResult(error="APPROVAL: contexte invalide (org/flow/execution manquant)")

    # Résoudre l'assignee : accepte un email ou un user_id directement
    assignee_id: PydanticObjectId | None = None
    if assignee_type == "user" and assignee_value:
        # essai email
        target = await User.find_one(User.email == assignee_value)
        if not target:
            try:
                target = await User.get(PydanticObjectId(assignee_value))
            except Exception:
                target = None
        if not target:
            return NodeResult(error=f"APPROVAL: utilisateur '{assignee_value}' introuvable")
        assignee_id = target.id

    expires_at = None
    if timeout_minutes:
        expires_at = datetime.now(UTC) + timedelta(minutes=int(timeout_minutes))

    task = ApprovalTask(
        flow_id=PydanticObjectId(flow_id),
        execution_id=PydanticObjectId(execution_id),
        node_id=node["id"],
        organization_id=PydanticObjectId(org_id),
        title=title,
        message=message,
        options=options,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
        status="pending",
        expires_at=expires_at,
        timeout_action=timeout_action,
    )
    await task.insert()

    # Envoie une notification action à l'assignee
    if assignee_id:
        try:
            await create_action_notification(
                recipient_user_id=str(assignee_id),
                title=title,
                message=message,
                actions=[
                    {"key": opt.get("value"), "label": opt.get("label")}
                    for opt in options
                ],
                action_payload={
                    "action_type": "flow_approval",
                    "approval_task_id": str(task.id),
                    "flow_id": flow_id,
                    "execution_id": execution_id,
                    "node_id": node["id"],
                },
                organization_id=org_id,
            )
        except Exception:
            pass

    # Signale au moteur de mettre en pause
    return NodeResult(
        pause=True,
        metadata={
            "approval_task_id": str(task.id),
            "assignee_type": assignee_type,
            "assignee_id": str(assignee_id) if assignee_id else None,
        },
    )
