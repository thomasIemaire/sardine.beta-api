"""Nœud notification — envoie une notification info aux destinataires."""

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult
from ..expressions import resolve_template


async def _resolve_targets(targets: list, org_id: str | None) -> list[str]:
    """Résout les cibles en liste de user_ids."""
    from app.core.enums import Status
    from app.features.organizations.models import Organization
    from app.features.teams.models import Team, TeamMember

    user_ids: set[str] = set()

    for target in targets:
        t_type = target.get("type")
        t_id = target.get("id")
        if not t_id:
            continue

        if t_type == "user":
            user_ids.add(str(t_id))

        elif t_type == "team":
            members = await TeamMember.find(
                TeamMember.team_id == PydanticObjectId(t_id),
                TeamMember.status == Status.ACTIVE,
            ).to_list()
            for m in members:
                user_ids.add(str(m.user_id))

        elif t_type == "organization":
            org = await Organization.get(PydanticObjectId(t_id))
            if not org:
                continue
            root_team = await Team.find_one(
                Team.organization_id == org.id,
                Team.is_root == True,  # noqa: E712
            )
            if root_team:
                members = await TeamMember.find(
                    TeamMember.team_id == root_team.id,
                    TeamMember.status == Status.ACTIVE,
                ).to_list()
                for m in members:
                    user_ids.add(str(m.user_id))

    return list(user_ids)


async def execute_notification(node: dict, context: ExecutionContext, engine) -> NodeResult:
    from app.features.notifications.service import create_info_notification

    config = node.get("config", {})
    title = resolve_template(config.get("title", ""), context)
    message = resolve_template(config.get("message", ""), context)
    targets = config.get("targets", [])

    if not title or not message:
        return NodeResult(error="NOTIFICATION: 'title' et 'message' requis")

    org_id = context.metadata.get("org_id")
    user_ids = await _resolve_targets(targets, org_id)

    if not user_ids:
        return NodeResult(output_port=0, metadata={"targets_count": 0})

    for uid in user_ids:
        try:
            await create_info_notification(
                recipient_user_id=uid,
                title=title,
                message=message,
                organization_id=org_id,
            )
        except Exception:
            continue

    return NodeResult(
        output_port=0,
        metadata={"targets_count": len(user_ids), "title": title},
    )
