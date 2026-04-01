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


async def list_user_organizations(user: User) -> list[Organization]:
    """
    Liste des organisations accessibles à l'utilisateur.
    Inclut : les organisations dont il est propriétaire
    + celles où il est membre d'une équipe.
    L'organisation privée apparaît en premier.
    """
    # 1. Organisations dont l'utilisateur est propriétaire
    owned = await Organization.find(Organization.owner_id == user.id).to_list()
    owned_ids = {str(o.id) for o in owned}

    # 2. Organisations via membership d'équipe
    memberships = await TeamMember.find(
        TeamMember.user_id == user.id,
        TeamMember.status == Status.ACTIVE,
    ).to_list()

    team_ids = [m.team_id for m in memberships]
    if team_ids:
        teams = await Team.find({"_id": {"$in": team_ids}}).to_list()
        org_ids = [t.organization_id for t in teams if str(t.organization_id) not in owned_ids]

        if org_ids:
            member_orgs = await Organization.find({"_id": {"$in": org_ids}}).to_list()
            owned.extend(member_orgs)

    # Tri : organisation privée en premier, puis par nom
    owned.sort(key=lambda o: (not o.is_private, o.name))
    return owned


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
