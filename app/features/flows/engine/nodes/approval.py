"""
Nœud approval — met le flow en pause et attend une réponse humaine.

Config attendu :
  {
    "title":        "Validation requise pour {{fileName}}",
    "message":      "Veuillez approuver ou rejeter ce document.",
    "options": [
      {"label": "Approuver", "value": "approved"},
      {"label": "Rejeter",   "value": "rejected"}
    ],
    "assigneeType": "user",
    "assigneeId":   "user@example.com",   // email ou user_id
    "timeout":      60,                   // minutes (optionnel)
    "timeoutAction": "reject",            // action si timeout
    "notifyChannels": ["inapp", "email"]  // défaut : ["inapp"]
  }
"""

import logging
from datetime import UTC, datetime, timedelta

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult
from ..expressions import resolve_template

logger = logging.getLogger(__name__)


async def execute_approval(node: dict, context: ExecutionContext, engine) -> NodeResult:
    from app.core.email import EmailError, send_email
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
    assignee_value = config.get("assigneeId", "").strip()
    timeout_minutes = config.get("timeout")
    timeout_action = config.get("timeoutAction", "reject")
    notify_channels: list[str] = config.get("notifyChannels", ["inapp"])

    org_id = context.metadata.get("org_id")
    flow_id = context.metadata.get("flow_id")
    execution_id = context.metadata.get("execution_id")

    print(f"[APPROVAL] ▶ title           : {title!r}")
    print(f"[APPROVAL] ▶ assigneeType    : {assignee_type!r}")
    print(f"[APPROVAL] ▶ assigneeId      : {assignee_value!r}")
    print(f"[APPROVAL] ▶ notifyChannels  : {notify_channels}")
    print(f"[APPROVAL] ▶ org_id          : {org_id}")
    print(f"[APPROVAL] ▶ flow_id         : {flow_id}")
    print(f"[APPROVAL] ▶ execution_id    : {execution_id}")

    if not org_id or not flow_id or not execution_id:
        return NodeResult(error="APPROVAL: contexte invalide (org/flow/execution manquant)")

    # Résoudre l'assignee : accepte un email ou un user_id
    assignee_user: User | None = None
    assignee_id: PydanticObjectId | None = None

    if assignee_type == "executor":
        triggered_by = context.metadata.get("triggered_by")
        if triggered_by:
            assignee_user = await User.get(PydanticObjectId(triggered_by))
        print(f"[APPROVAL] ▶ assignee résolu (executor) : {assignee_user}")
        if not assignee_user:
            return NodeResult(error="APPROVAL: executor introuvable dans le contexte")
        assignee_id = assignee_user.id

    elif assignee_type == "user" and assignee_value:
        # Essai par email d'abord, puis par user_id
        assignee_user = await User.find_one(User.email == assignee_value)
        if not assignee_user:
            try:
                assignee_user = await User.get(PydanticObjectId(assignee_value))
            except Exception:
                pass
        print(f"[APPROVAL] ▶ assignee résolu (user) : {assignee_user}")
        if not assignee_user:
            return NodeResult(error=f"APPROVAL: utilisateur '{assignee_value}' introuvable")
        assignee_id = assignee_user.id

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

    # ── Notifications à l'assignee ───────────────────────────────
    if assignee_id:
        action_payload = {
            "action_type": "flow_approval",
            "approval_task_id": str(task.id),
            "flow_id": flow_id,
            "execution_id": execution_id,
            "node_id": node["id"],
        }

        # Notification in-app (avec boutons d'action)
        if "inapp" in notify_channels:
            print(f"[APPROVAL] → envoi in-app à user={assignee_id}")
            try:
                await create_action_notification(
                    recipient_user_id=str(assignee_id),
                    title=title,
                    message=message,
                    actions=[
                        {"key": opt.get("value"), "label": opt.get("label")}
                        for opt in options
                    ],
                    action_payload=action_payload,
                    organization_id=org_id,
                )
                print(f"[APPROVAL] ✓ in-app envoyée à user={assignee_id}")
            except Exception as exc:
                print(f"[APPROVAL] ✗ échec in-app : {exc}")

        # Notification email
        if "email" in notify_channels and assignee_user:
            try:
                options_html = "".join(
                    f"<li><strong>{opt.get('label')}</strong></li>" for opt in options
                )
                html_content = f"""
                    <p>{message}</p>
                    <p>Actions disponibles :</p>
                    <ul>{options_html}</ul>
                    <p>Connectez-vous à l'application pour répondre.</p>
                """
                full_name = f"{assignee_user.first_name} {assignee_user.last_name}".strip()
                await send_email(
                    to=[{"email": assignee_user.email, "name": full_name}] if full_name else [{"email": assignee_user.email}],
                    subject=title,
                    html_content=html_content,
                )
                logger.info("APPROVAL: email envoyé à %s", assignee_user.email)
            except EmailError as exc:
                logger.error("APPROVAL: échec email — %s", exc)
            except Exception as exc:
                logger.error("APPROVAL: erreur inattendue email — %s", exc)

    # Signale au moteur de mettre en pause
    return NodeResult(
        pause=True,
        metadata={
            "approval_task_id": str(task.id),
            "assignee_type": assignee_type,
            "assignee_id": str(assignee_id) if assignee_id else None,
            "notify_channels": notify_channels,
        },
    )
