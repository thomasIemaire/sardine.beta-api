"""
Nœud notification — envoie une notification in-app et/ou par email aux destinataires.

Config attendu :
  {
    "title":   "Fichier traité : {{fileName}}",
    "message": "Résultat : {{classificationResult.mappedClass}}",
    "targets": [
      { "type": "executor" },
      { "type": "user", "id": "<user_id>" },
      { "type": "user", "email": "user@example.com" },
      { "type": "team", "id": "<team_id>" },
      { "type": "organization", "id": "<org_id>" }
    ],
    "channels": ["inapp", "email"]   // défaut : ["inapp"]
  }

Types de targets :
  - executor      : déclencheur du flow (pas d'id requis)
  - user          : utilisateur par id OU par email
  - team          : tous les membres actifs de l'équipe
  - organization  : tous les membres actifs de l'équipe racine de l'org

Si targets est vide, notifie l'executor par défaut.
1 port de sortie (port 0), toujours.
"""

import logging

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult
from ..expressions import resolve_template

logger = logging.getLogger(__name__)


async def _resolve_targets(targets: list, org_id: str | None, triggered_by: str | None) -> list[str]:
    """Résout les cibles en liste de user_ids dédupliqués."""
    from app.core.enums import Status
    from app.features.auth.models import User as UserModel
    from app.features.organizations.models import Organization
    from app.features.teams.models import Team, TeamMember

    user_ids: set[str] = set()

    for target in targets:
        t_type = target.get("type")

        # ── executor : déclencheur du flow ───────────────────────
        if t_type == "executor":
            if triggered_by:
                user_ids.add(str(triggered_by))
            else:
                logger.warning("NOTIFICATION: executor demandé mais triggered_by absent du contexte")

        # ── user : par id ou par email ────────────────────────────
        elif t_type == "user":
            t_id = target.get("id", "").strip()
            t_email = target.get("email", "").strip() or target.get("name", "").strip()

            if t_id:
                user_ids.add(t_id)
            elif t_email and "@" in t_email:
                user = await UserModel.find_one(UserModel.email == t_email)
                if user:
                    user_ids.add(str(user.id))
                else:
                    logger.warning("NOTIFICATION: user email=%s introuvable", t_email)
            else:
                logger.warning("NOTIFICATION: user sans 'id' ni 'email' ignoré — %s", target)

        # ── team : tous les membres actifs ────────────────────────
        elif t_type == "team":
            t_id = target.get("id", "").strip()
            if not t_id:
                logger.warning("NOTIFICATION: team sans 'id' ignoré")
                continue
            try:
                members = await TeamMember.find(
                    TeamMember.team_id == PydanticObjectId(t_id),
                    TeamMember.status == Status.ACTIVE,
                ).to_list()
                for m in members:
                    user_ids.add(str(m.user_id))
                logger.debug("NOTIFICATION: team %s → %d membres", t_id, len(members))
            except Exception as exc:
                logger.error("NOTIFICATION: erreur résolution team %s — %s", t_id, exc)

        # ── organization : tous les membres de l'équipe racine ────
        elif t_type == "organization":
            t_id = target.get("id", "").strip()
            if not t_id:
                logger.warning("NOTIFICATION: organization sans 'id' ignoré")
                continue
            try:
                org = await Organization.get(PydanticObjectId(t_id))
                if not org:
                    logger.warning("NOTIFICATION: organisation %s introuvable", t_id)
                    continue
                root_team = await Team.find_one(
                    Team.organization_id == org.id,
                    Team.is_root == True,  # noqa: E712
                )
                if not root_team:
                    logger.warning("NOTIFICATION: équipe racine introuvable pour l'org %s", t_id)
                    continue
                members = await TeamMember.find(
                    TeamMember.team_id == root_team.id,
                    TeamMember.status == Status.ACTIVE,
                ).to_list()
                for m in members:
                    user_ids.add(str(m.user_id))
                logger.debug("NOTIFICATION: org %s → %d membres", t_id, len(members))
            except Exception as exc:
                logger.error("NOTIFICATION: erreur résolution org %s — %s", t_id, exc)

        else:
            logger.warning("NOTIFICATION: type de target inconnu '%s'", t_type)

    return list(user_ids)


async def _get_user_email(user_id: str) -> tuple[str, str] | None:
    """Retourne (email, nom_complet) d'un utilisateur, ou None si introuvable."""
    from app.features.auth.models import User
    user = await User.get(PydanticObjectId(user_id))
    if not user:
        logger.warning("NOTIFICATION: utilisateur %s introuvable pour l'email", user_id)
        return None
    return user.email, f"{user.first_name} {user.last_name}".strip()


async def execute_notification(node: dict, context: ExecutionContext, engine) -> NodeResult:
    from app.core.email import EmailError, send_email
    from app.features.notifications.service import create_info_notification

    config = node.get("config", {})
    title = resolve_template(config.get("title", ""), context)
    message = resolve_template(config.get("message", ""), context)
    targets = config.get("targets", [])
    channels: list[str] = config.get("channels", ["inapp"])

    print(f"[NOTIFICATION] ▶ config brut     : {config}")
    print(f"[NOTIFICATION] ▶ title résolu    : {title!r}")
    print(f"[NOTIFICATION] ▶ message résolu  : {message!r}")
    print(f"[NOTIFICATION] ▶ targets         : {targets}")
    print(f"[NOTIFICATION] ▶ channels        : {channels}")
    print(f"[NOTIFICATION] ▶ triggered_by    : {context.metadata.get('triggered_by')}")
    print(f"[NOTIFICATION] ▶ org_id          : {context.metadata.get('org_id')}")

    if not title:
        return NodeResult(error="NOTIFICATION: champ 'title' requis")
    if not message:
        return NodeResult(error="NOTIFICATION: champ 'message' requis")

    use_inapp = "inapp" in channels
    use_email = "email" in channels

    org_id = context.metadata.get("org_id")
    triggered_by = context.metadata.get("triggered_by")

    # Si aucune cible configurée, notifier le déclencheur du flow par défaut
    effective_targets = targets or [{"type": "triggered_by"}]
    print(f"[NOTIFICATION] ▶ effective_targets : {effective_targets}")

    user_ids = await _resolve_targets(effective_targets, org_id, triggered_by)
    print(f"[NOTIFICATION] ▶ user_ids résolus  : {user_ids}")

    if not user_ids:
        print("[NOTIFICATION] ✗ Aucun destinataire résolu — la notification ne sera pas envoyée")
        return NodeResult(output_port=0, metadata={"targets_count": 0, "warning": "aucun destinataire résolu"})

    emails_sent = 0
    inapp_sent = 0
    errors: list[str] = []

    for uid in user_ids:
        # Notification in-app
        if use_inapp:
            try:
                print(f"[NOTIFICATION] → envoi in-app à user={uid}")
                await create_info_notification(
                    recipient_user_id=uid,
                    title=title,
                    message=message,
                    organization_id=org_id,
                )
                inapp_sent += 1
                print(f"[NOTIFICATION] ✓ in-app envoyée à user={uid}")
            except Exception as exc:
                msg = f"inapp user={uid}: {exc}"
                print(f"[NOTIFICATION] ✗ échec in-app : {msg}")
                errors.append(msg)

        # Notification email
        if use_email:
            try:
                user_info = await _get_user_email(uid)
                if user_info:
                    email, full_name = user_info
                    print(f"[NOTIFICATION] → envoi email à {email}")
                    await send_email(
                        to=[{"email": email, "name": full_name}] if full_name else [{"email": email}],
                        subject=title,
                        html_content=f"<p>{message}</p>",
                    )
                    emails_sent += 1
                    print(f"[NOTIFICATION] ✓ email envoyé à {email}")
                else:
                    msg = f"email user={uid}: utilisateur introuvable"
                    print(f"[NOTIFICATION] ✗ {msg}")
                    errors.append(msg)
            except EmailError as exc:
                msg = f"email user={uid}: {exc}"
                print(f"[NOTIFICATION] ✗ échec email : {msg}")
                errors.append(msg)
            except Exception as exc:
                msg = f"email user={uid}: erreur inattendue — {exc}"
                print(f"[NOTIFICATION] ✗ {msg}")
                errors.append(msg)

    print(f"[NOTIFICATION] ✓ terminé — inapp_sent={inapp_sent} emails_sent={emails_sent} errors={errors}")

    metadata = {
        "targets_count": len(user_ids),
        "inapp_sent": inapp_sent,
        "emails_sent": emails_sent,
        "title": title,
    }
    if errors:
        metadata["errors"] = errors

    return NodeResult(output_port=0, metadata=metadata)
