"""
Service organisations.

Lors de la création d'une organisation (privée ou collaborative),
le système crée automatiquement :
  1. Un dossier racine
  2. Un dossier corbeille
  3. Une équipe racine avec le créateur comme propriétaire
"""

from datetime import datetime, timezone

from beanie import PydanticObjectId

import logging

from app.core.audit import log_action
from app.core.enums import NotificationActionStatus, Status, TeamMemberRole, UserRole
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_password
from app.core.validators import validate_password
from app.features.auth.models import User

logger = logging.getLogger(__name__)
from app.features.folders.models import Folder
from app.features.notifications.service import (
    create_action_notification,
    register_action_handler,
)
from app.features.organizations.models import Organization
from app.features.organizations.schemas import OrganizationCreate, OrganizationUpdate
from app.features.teams.models import Team, TeamMember


async def _bootstrap_organization(org: Organization, user: User) -> None:
    """
    Initialisation commune à toute nouvelle organisation :
    crée le dossier racine, la corbeille, l'équipe racine
    et ajoute le créateur comme propriétaire de l'équipe racine.
    """
    # 1. Dossier racine
    root_folder = Folder(
        name="Racine",
        organization_id=org.id,
        is_root=True,
    )
    await root_folder.insert()

    # 2. Dossier corbeille (au même niveau que la racine, séparé de l'arborescence)
    trash_folder = Folder(
        name="Corbeille",
        organization_id=org.id,
        is_trash=True,
    )
    await trash_folder.insert()

    # 3. Équipe racine
    root_team = Team(
        name=f"Équipe {org.name}",
        organization_id=org.id,
        is_root=True,
    )
    await root_team.insert()

    # 4. Le créateur est propriétaire de l'équipe racine
    membership = TeamMember(
        team_id=root_team.id,
        user_id=user.id,
        role=TeamMemberRole.OWNER,
        status=Status.ACTIVE,
    )
    await membership.insert()


async def create_private_organization(user: User) -> Organization:
    """
    Crée l'organisation privée "Prénom Nom" à l'inscription.
    Appelée automatiquement par le service auth lors du register.
    """
    org = Organization(
        name=f"{user.first_name} {user.last_name}",
        owner_id=user.id,
        is_private=True,
        status=Status.ACTIVE,
    )
    await org.insert()
    await _bootstrap_organization(org, user)
    return org


async def create_organization(user: User, payload: OrganizationCreate) -> Organization:
    """
    Création d'une organisation collaborative.
    Même bootstrap que l'org privée (dossier racine, corbeille, équipe racine).
    """
    org = Organization(
        name=payload.name,
        owner_id=user.id,
        is_private=False,
        status=Status.ACTIVE,
        external_reference=payload.external_reference,
        distributor_org_id=(
            PydanticObjectId(payload.distributor_org_id)
            if payload.distributor_org_id else None
        ),
        parent_org_id=(
            PydanticObjectId(payload.parent_org_id)
            if payload.parent_org_id else None
        ),
    )
    await org.insert()
    await _bootstrap_organization(org, user)
    return org


async def update_organization(
    user: User,
    org_id: str,
    payload: OrganizationUpdate,
) -> Organization:
    """
    Modification d'une organisation.
    Seul le propriétaire de l'organisation peut modifier.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")

    # Seul le propriétaire peut modifier
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError("Seul le propriétaire peut modifier cette organisation")

    update_data = payload.model_dump(exclude_unset=True)

    # Conversion des IDs string en PydanticObjectId si présents
    if "distributor_org_id" in update_data and update_data["distributor_org_id"]:
        update_data["distributor_org_id"] = PydanticObjectId(update_data["distributor_org_id"])
    if "parent_org_id" in update_data and update_data["parent_org_id"]:
        update_data["parent_org_id"] = PydanticObjectId(update_data["parent_org_id"])

    update_data["updated_at"] = datetime.now(timezone.utc)
    await org.set(update_data)
    return org


async def list_user_organizations(user: User) -> list[dict]:
    """
    Liste des organisations visibles par l'utilisateur.
    Inclut les orgs dont il est propriétaire (toujours actif)
    + celles où il est membre (actif ou inactif).
    Retourne des dicts {org, is_active_member} pour que le front
    puisse afficher un cadenas sur les orgs désactivées.
    """
    result_map: dict[str, dict] = {}

    # 1. Organisations dont l'utilisateur est propriétaire → toujours actif
    owned = await Organization.find(Organization.owner_id == user.id).to_list()
    for o in owned:
        result_map[str(o.id)] = {"org": o, "is_active_member": True}

    # 2. Organisations via membership d'équipe (actif ET inactif)
    memberships = await TeamMember.find(
        TeamMember.user_id == user.id,
    ).to_list()

    team_ids = [m.team_id for m in memberships]
    if team_ids:
        teams = await Team.find(
            {"_id": {"$in": team_ids}, "is_root": True}
        ).to_list()

        # Map team_id → membership status
        team_status = {str(m.team_id): m.status for m in memberships}

        org_ids_to_fetch = [
            t.organization_id for t in teams
            if str(t.organization_id) not in result_map
        ]

        if org_ids_to_fetch:
            member_orgs = await Organization.find(
                {"_id": {"$in": org_ids_to_fetch}}
            ).to_list()
            for o in member_orgs:
                # Trouver le statut via la root team de cette org
                team_for_org = next(
                    (t for t in teams if str(t.organization_id) == str(o.id)), None
                )
                is_active = (
                    team_status.get(str(team_for_org.id)) == Status.ACTIVE
                    if team_for_org else False
                )
                result_map[str(o.id)] = {"org": o, "is_active_member": is_active}

    # Tri : org privée en premier, puis par nom
    items = list(result_map.values())
    items.sort(key=lambda x: (not x["org"].is_private, x["org"].name))
    return items


async def list_owned_organizations(user: User) -> list[Organization]:
    """
    Liste des organisations dont l'utilisateur est propriétaire (admin).
    L'organisation privée apparaît en premier.
    """
    orgs = await Organization.find(Organization.owner_id == user.id).to_list()
    orgs.sort(key=lambda o: (not o.is_private, o.name))
    return orgs


async def list_child_organizations(user: User, org_id: str) -> list[Organization]:
    """
    Liste des organisations enfants (clientes) d'une organisation.
    L'utilisateur doit être propriétaire de l'organisation parente.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError("Seul le propriétaire peut voir les organisations enfants")

    children = await Organization.find(
        Organization.parent_org_id == org.id
    ).to_list()
    children.sort(key=lambda o: o.name)
    return children


async def list_distributed_organizations(user: User, org_id: str) -> list[Organization]:
    """
    Liste des organisations distribuées par une organisation.
    L'utilisateur doit être propriétaire de l'organisation distributrice.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(user.id):
        raise ForbiddenError("Seul le propriétaire peut voir les organisations distribuées")

    distributed = await Organization.find(
        Organization.distributor_org_id == org.id
    ).to_list()
    distributed.sort(key=lambda o: o.name)
    return distributed


async def list_organization_members(user: User, org_id: str) -> list[dict]:
    """
    Liste tous les membres d'une organisation via l'équipe racine.
    L'utilisateur doit être membre de l'organisation.
    Retourne les membres directs de l'équipe racine avec leurs infos.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")

    # Vérifier que l'utilisateur est membre (owner ou via équipe)
    is_owner = str(org.owner_id) == str(user.id)
    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if not root_team:
        raise NotFoundError("Équipe racine introuvable")

    if not is_owner:
        membership = await TeamMember.find_one(
            TeamMember.team_id == root_team.id,
            TeamMember.user_id == user.id,
            TeamMember.status == Status.ACTIVE,
        )
        if not membership:
            raise ForbiddenError("Vous n'êtes pas membre de cette organisation")

    # Récupérer tous les membres de l'équipe racine
    members = await TeamMember.find(TeamMember.team_id == root_team.id).to_list()

    result = []
    for member in members:
        member_user = await User.get(member.user_id)
        if member_user:
            result.append({"member": member, "user": member_user})

    return result


async def update_member_status(
    owner: User, org_id: str, target_user_id: str, new_status: int,
) -> TeamMember:
    """
    Active ou désactive un membre d'une organisation.
    Owner requis. On ne peut pas désactiver le owner de l'org.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(owner.id):
        raise ForbiddenError("Seul le propriétaire peut modifier le statut des membres")

    # Empêcher de se désactiver soi-même (owner)
    if str(target_user_id) == str(owner.id) and new_status == Status.INACTIVE:
        raise ForbiddenError("Le propriétaire ne peut pas être désactivé")

    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if not root_team:
        raise NotFoundError("Équipe racine introuvable")

    membership = await TeamMember.find_one(
        TeamMember.team_id == root_team.id,
        TeamMember.user_id == PydanticObjectId(target_user_id),
    )
    if not membership:
        raise NotFoundError("Membre non trouvé dans cette organisation")

    if membership.status == new_status:
        raise ConflictError(
            "Ce membre est déjà actif" if new_status == Status.ACTIVE
            else "Ce membre est déjà désactivé"
        )

    from datetime import datetime, timezone
    membership.status = new_status
    await membership.save()

    action = "MEMBER_ACTIVATE" if new_status == Status.ACTIVE else "MEMBER_DEACTIVATE"
    target = await User.get(PydanticObjectId(target_user_id))
    detail = f"Membre {target.email if target else target_user_id}"
    await log_action(
        user_id=owner.id,
        action=action,
        details=f"{detail} {'activé' if new_status == Status.ACTIVE else 'désactivé'} dans « {org.name} »",
        organization_id=org.id,
    )

    return membership


async def update_member_role(
    owner: User, org_id: str, target_user_id: str, new_role: int,
) -> TeamMember:
    """
    Change le rôle d'un membre dans l'équipe racine de l'organisation.
    Owner requis. Il doit toujours rester au moins un Owner.
    """
    from app.core.enums import TEAM_ROLE_LABELS

    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(owner.id):
        raise ForbiddenError("Seul le propriétaire peut modifier les rôles")

    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if not root_team:
        raise NotFoundError("Équipe racine introuvable")

    membership = await TeamMember.find_one(
        TeamMember.team_id == root_team.id,
        TeamMember.user_id == PydanticObjectId(target_user_id),
    )
    if not membership:
        raise NotFoundError("Membre non trouvé dans cette organisation")

    if membership.role == new_role:
        raise ConflictError("Ce membre a déjà ce rôle")

    # Si on rétrograde un Owner → vérifier qu'il en reste au moins un autre
    if membership.role == TeamMemberRole.OWNER and new_role == TeamMemberRole.MEMBER:
        owner_count = await TeamMember.find(
            TeamMember.team_id == root_team.id,
            TeamMember.role == TeamMemberRole.OWNER,
            TeamMember.status == Status.ACTIVE,
        ).count()
        if owner_count <= 1:
            raise ForbiddenError("Il doit rester au moins un propriétaire dans l'organisation")

    membership.role = new_role
    await membership.save()

    target = await User.get(PydanticObjectId(target_user_id))
    role_label = TEAM_ROLE_LABELS.get(new_role, str(new_role))
    await log_action(
        user_id=owner.id,
        action="MEMBER_ROLE_CHANGE",
        details=f"{target.email if target else target_user_id} → {role_label} dans « {org.name} »",
        organization_id=org.id,
    )

    return membership


# ─── Invitations ─────────────────────────────────────────────────

async def invite_user_to_organization(
    owner: User, org_id: str, target_user_id: str,
) -> None:
    """
    Invite un utilisateur à rejoindre une organisation.
    Crée une notification action avec les boutons Accepter / Refuser.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(owner.id):
        raise ForbiddenError("Seul le propriétaire peut inviter des membres")
    if org.is_private:
        raise ForbiddenError("Impossible d'inviter dans une organisation privée")

    target = await User.get(PydanticObjectId(target_user_id))
    if not target:
        raise NotFoundError("Utilisateur non trouvé")

    # Vérifier que l'utilisateur n'est pas déjà membre (via l'équipe racine)
    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if root_team:
        existing = await TeamMember.find_one(
            TeamMember.team_id == root_team.id,
            TeamMember.user_id == target.id,
            TeamMember.status == Status.ACTIVE,
        )
        if existing:
            raise ConflictError("Cet utilisateur est déjà membre de l'organisation")

    await create_action_notification(
        recipient_user_id=str(target.id),
        title="Invitation à rejoindre une organisation",
        message=(
            f"{owner.first_name} {owner.last_name} vous invite à rejoindre "
            f"l'organisation « {org.name} »."
        ),
        actions=[
            {"key": "accept", "label": "Accepter"},
            {"key": "reject", "label": "Refuser"},
        ],
        action_payload={
            "action_type": "org_invitation",
            "organization_id": str(org.id),
            "invited_by": str(owner.id),
        },
    )


async def _handle_org_invitation(notif, action_key: str) -> str:
    """
    Handler métier pour les invitations d'organisation.
    Si accepté : ajoute l'utilisateur à l'équipe racine de l'organisation.
    """
    if action_key == "accept":
        org_id = notif.action_payload.get("organization_id")
        org = await Organization.get(PydanticObjectId(org_id))
        if not org:
            return NotificationActionStatus.REJECTED

        # Trouver l'équipe racine
        root_team = await Team.find_one(
            Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
        )
        if not root_team:
            return NotificationActionStatus.REJECTED

        # Vérifier que l'utilisateur n'est pas déjà membre
        existing = await TeamMember.find_one(
            TeamMember.team_id == root_team.id,
            TeamMember.user_id == notif.recipient_user_id,
        )
        if not existing:
            membership = TeamMember(
                team_id=root_team.id,
                user_id=notif.recipient_user_id,
                role=TeamMemberRole.MEMBER,
                status=Status.ACTIVE,
            )
            await membership.insert()

        return NotificationActionStatus.ACCEPTED

    return NotificationActionStatus.REJECTED


# Enregistrement du handler d'invitation
register_action_handler("org_invitation", _handle_org_invitation)


# ─── Invitation en masse ────────────────────────────────────────

async def bulk_invite_members(
    owner: User,
    org_id: str,
    members: list[dict],
) -> list[dict]:
    """
    Invitation en masse : pour chaque entrée (email + password),
    crée le compte s'il n'existe pas, puis l'ajoute à l'équipe racine.
    """
    org = await Organization.get(PydanticObjectId(org_id))
    if not org:
        raise NotFoundError("Organisation non trouvée")
    if str(org.owner_id) != str(owner.id):
        raise ForbiddenError("Seul le propriétaire peut inviter des membres")

    root_team = await Team.find_one(
        Team.organization_id == org.id, Team.is_root == True,  # noqa: E712
    )
    if not root_team:
        raise NotFoundError("Équipe racine introuvable")

    results = []

    for entry in members:
        email = entry.email
        password = entry.password

        try:
            # Valider le mot de passe
            validate_password(password)

            # Chercher si l'utilisateur existe déjà
            user = await User.find_one(User.email == email)
            status_label = "existing"

            if not user:
                # Créer le compte
                user = User(
                    email=email,
                    hashed_password=hash_password(password),
                    first_name=email.split("@")[0],
                    last_name="",
                    role=UserRole.USER,
                    status=Status.ACTIVE,
                )
                await user.insert()

                # Générer l'avatar
                from app.core.avatar import generate_avatar
                avatar_path = generate_avatar(str(user.id))
                await user.set({"avatar_path": avatar_path})

                # Créer l'organisation privée (comme à l'inscription)
                await create_private_organization(user)

                status_label = "created"

            # Vérifier s'il est déjà membre de l'équipe racine
            existing_member = await TeamMember.find_one(
                TeamMember.team_id == root_team.id,
                TeamMember.user_id == user.id,
            )
            if existing_member:
                if existing_member.status == Status.ACTIVE:
                    results.append({
                        "email": email,
                        "status": "existing",
                        "detail": "Déjà membre de l'organisation",
                    })
                    continue
                # Réactiver si inactif
                existing_member.status = Status.ACTIVE
                await existing_member.save()
            else:
                # Ajouter à l'équipe racine
                membership = TeamMember(
                    team_id=root_team.id,
                    user_id=user.id,
                    role=TeamMemberRole.MEMBER,
                    status=Status.ACTIVE,
                )
                await membership.insert()

            results.append({
                "email": email,
                "status": status_label,
                "detail": "Compte créé et ajouté" if status_label == "created"
                          else "Utilisateur existant ajouté",
            })

        except Exception as e:
            logger.warning("bulk_invite error for %s: %s", email, e)
            results.append({
                "email": email,
                "status": "error",
                "detail": str(e),
            })

    await log_action(
        user_id=owner.id,
        action="BULK_INVITE",
        details=f"{len(members)} membre(s) invité(s) dans « {org.name} »",
        organization_id=org.id,
    )

    return results
