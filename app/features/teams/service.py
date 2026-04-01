"""
Service équipes — gestion des équipes, membres, sous-équipes
et héritage des droits propriétaire.

L'héritage descendant est le point clé :
un propriétaire d'une équipe parente est automatiquement considéré
propriétaire de toutes les sous-équipes enfants, récursivement.
Ce calcul est dynamique (jamais dupliqué en base).
"""

from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.enums import Status, TeamMemberRole
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.features.auth.models import User
from app.features.teams.models import Team, TeamHierarchy, TeamMember


# ─── Équipes ──────────────────────────────────────────────────────

async def create_team(user: User, org_id: str, name: str) -> Team:
    """
    Créer une équipe dans une organisation.
    Le créateur est automatiquement propriétaire.
    """
    team = Team(
        name=name,
        organization_id=PydanticObjectId(org_id),
    )
    await team.insert()

    # Le créateur devient propriétaire de la nouvelle équipe
    membership = TeamMember(
        team_id=team.id,
        user_id=user.id,
        role=TeamMemberRole.OWNER,
        status=Status.ACTIVE,
    )
    await membership.insert()
    return team


async def list_user_teams(user: User, org_id: str) -> list[dict]:
    """
    Liste des équipes de l'organisation auxquelles l'utilisateur appartient.
    Retourne chaque équipe avec le rôle de l'utilisateur.
    """
    # Toutes les équipes de l'organisation
    org_teams = await Team.find(
        Team.organization_id == PydanticObjectId(org_id)
    ).to_list()

    # Memberships actifs de l'utilisateur
    memberships = await TeamMember.find(
        TeamMember.user_id == user.id,
        TeamMember.status == Status.ACTIVE,
    ).to_list()
    membership_map = {str(m.team_id): m for m in memberships}

    result = []
    for team in org_teams:
        if str(team.id) in membership_map:
            result.append({
                "team": team,
                "role": membership_map[str(team.id)].role,
            })

    return result


async def update_team(user: User, team_id: str, name: str) -> Team:
    """Renommer une équipe (hors équipe racine)."""
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")
    if team.is_root:
        raise ForbiddenError("L'équipe racine ne peut pas être renommée")

    # Vérifier que l'utilisateur est propriétaire (direct ou hérité)
    await _assert_is_owner(user.id, team.id)

    await team.set({"name": name, "updated_at": datetime.now(timezone.utc)})
    return team


async def delete_team(user: User, team_id: str) -> None:
    """
    Supprimer une équipe (hors équipe racine).
    La suppression d'une équipe parente ne supprime PAS les sous-équipes ;
    elle retire uniquement les liens hiérarchiques.
    """
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")
    if team.is_root:
        raise ForbiddenError("L'équipe racine ne peut pas être supprimée")

    await _assert_is_owner(user.id, team.id)

    # Nettoyage des liens hiérarchiques (parent et enfant)
    await TeamHierarchy.find(
        {"$or": [
            {"parent_team_id": team.id},
            {"child_team_id": team.id},
        ]}
    ).delete()

    # Suppression des memberships associés
    await TeamMember.find(TeamMember.team_id == team.id).delete()

    await team.delete()
    await log_action(user.id, "TEAM_DELETE", f"Team '{team.name}' deleted")


# ─── Membres ──────────────────────────────────────────────────────

async def add_member(owner: User, team_id: str, target_user_id: str) -> TeamMember:
    """
    Ajouter un membre à une équipe.
    Rôle par défaut : Membre (2). Statut : Actif (1).
    Un utilisateur ne peut pas être ajouté deux fois (unicité team_id + user_id).
    """
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")

    await _assert_is_owner(owner.id, team.id)

    # Vérifier que l'utilisateur cible existe
    target = await User.get(PydanticObjectId(target_user_id))
    if not target:
        raise NotFoundError("Utilisateur non trouvé")

    # Unicité (team_id + user_id)
    existing = await TeamMember.find_one(
        TeamMember.team_id == team.id,
        TeamMember.user_id == target.id,
    )
    if existing:
        raise ConflictError("Cet utilisateur est déjà membre de l'équipe")

    membership = TeamMember(
        team_id=team.id,
        user_id=target.id,
        role=TeamMemberRole.MEMBER,
        status=Status.ACTIVE,
    )
    await membership.insert()

    # Notification info au membre ajouté
    from app.features.notifications.service import create_info_notification
    await create_info_notification(
        recipient_user_id=str(target.id),
        title="Ajout à une équipe",
        message=f"Vous avez été ajouté à l'équipe « {team.name} ».",
        organization_id=str(team.organization_id),
    )

    return membership


async def update_member_role(
    owner: User, team_id: str, target_user_id: str, new_role: int,
) -> TeamMember:
    """
    Changer le rôle d'un membre (Propriétaire ↔ Membre).
    Contrainte : il doit toujours rester au moins un propriétaire.
    """
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")

    await _assert_is_owner(owner.id, team.id)

    membership = await TeamMember.find_one(
        TeamMember.team_id == team.id,
        TeamMember.user_id == PydanticObjectId(target_user_id),
    )
    if not membership:
        raise NotFoundError("Membre non trouvé dans cette équipe")

    # Protection : au moins un propriétaire doit rester
    if membership.role == TeamMemberRole.OWNER and new_role == TeamMemberRole.MEMBER:
        owner_count = await TeamMember.find(
            TeamMember.team_id == team.id,
            TeamMember.role == TeamMemberRole.OWNER,
        ).count()
        if owner_count <= 1:
            raise ForbiddenError("Il doit rester au moins un propriétaire dans l'équipe")

    await membership.set({"role": new_role})
    await log_action(
        owner.id, "MEMBER_ROLE_CHANGE",
        f"User {target_user_id} role → {new_role} in team {team.name}",
    )

    # Notification info au membre dont le rôle change
    from app.features.notifications.service import create_info_notification
    role_label = "Propriétaire" if new_role == TeamMemberRole.OWNER else "Membre"
    await create_info_notification(
        recipient_user_id=target_user_id,
        title="Changement de rôle",
        message=f"Votre rôle dans l'équipe « {team.name} » a été modifié en {role_label}.",
        organization_id=str(team.organization_id),
    )

    return membership


async def update_member_status(
    owner: User, team_id: str, target_user_id: str, new_status: int,
) -> TeamMember:
    """
    Activer/désactiver un membre.
    Le dernier propriétaire ne peut pas être désactivé.
    """
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")

    await _assert_is_owner(owner.id, team.id)

    membership = await TeamMember.find_one(
        TeamMember.team_id == team.id,
        TeamMember.user_id == PydanticObjectId(target_user_id),
    )
    if not membership:
        raise NotFoundError("Membre non trouvé dans cette équipe")

    # Le dernier propriétaire ne peut pas être retiré
    if membership.role == TeamMemberRole.OWNER and new_status == Status.INACTIVE:
        owner_count = await TeamMember.find(
            TeamMember.team_id == team.id,
            TeamMember.role == TeamMemberRole.OWNER,
            TeamMember.status == Status.ACTIVE,
        ).count()
        if owner_count <= 1:
            raise ForbiddenError("Le dernier propriétaire ne peut pas être désactivé")

    await membership.set({"status": new_status})

    # US-PERM-12 : nettoyage des droits individuels lors de la désactivation
    if new_status == Status.INACTIVE:
        from app.features.permissions.service import cleanup_member_permissions
        await cleanup_member_permissions(target_user_id, team_id)

    return membership


async def list_team_members(team_id: str) -> list[dict]:
    """
    Liste des membres d'une équipe.
    Inclut les propriétaires hérités des équipes parentes.
    """
    team = await Team.get(PydanticObjectId(team_id))
    if not team:
        raise NotFoundError("Équipe non trouvée")

    # 1. Membres directs
    direct_members = await TeamMember.find(TeamMember.team_id == team.id).to_list()

    result = []
    seen_user_ids = set()

    for member in direct_members:
        user = await User.get(member.user_id)
        if user:
            result.append({"member": member, "user": user, "inherited": False})
            seen_user_ids.add(str(member.user_id))

    # 2. Propriétaires hérités des équipes parentes
    inherited_owners = await _get_inherited_owners(team.id)
    for owner_user in inherited_owners:
        if str(owner_user.id) not in seen_user_ids:
            # Créer un "membre virtuel" pour l'affichage
            virtual_member = TeamMember(
                team_id=team.id,
                user_id=owner_user.id,
                role=TeamMemberRole.OWNER,
                status=Status.ACTIVE,
            )
            result.append({"member": virtual_member, "user": owner_user, "inherited": True})

    return result


# ─── Sous-équipes & héritage ──────────────────────────────────────

async def create_sub_team(
    user: User, org_id: str, name: str, parent_team_ids: list[str],
) -> Team:
    """
    Créer une sous-équipe rattachée à un ou plusieurs parents.
    Relation N-N : une équipe peut avoir plusieurs parents.
    """
    # Vérifier que l'utilisateur est propriétaire de chaque équipe parente
    for pid in parent_team_ids:
        parent = await Team.get(PydanticObjectId(pid))
        if not parent:
            raise NotFoundError(f"Équipe parente {pid} non trouvée")
        await _assert_is_owner(user.id, parent.id)

    # Création de la sous-équipe
    sub_team = await create_team(user, org_id, name)

    # Création des liens hiérarchiques
    for pid in parent_team_ids:
        link = TeamHierarchy(
            parent_team_id=PydanticObjectId(pid),
            child_team_id=sub_team.id,
        )
        await link.insert()

    return sub_team


async def get_team_tree(org_id: str, user: User) -> list[dict]:
    """
    Arborescence complète des équipes de l'organisation.
    Construit un arbre à partir des liens hiérarchiques.
    """
    all_teams = await Team.find(
        Team.organization_id == PydanticObjectId(org_id)
    ).to_list()

    team_ids = [t.id for t in all_teams]
    all_hierarchies = await TeamHierarchy.find(
        {"parent_team_id": {"$in": team_ids}}
    ).to_list()

    # Memberships de l'utilisateur courant pour le flag "is_member"
    user_memberships = await TeamMember.find(
        TeamMember.user_id == user.id,
        TeamMember.status == Status.ACTIVE,
    ).to_list()
    user_team_ids = {str(m.team_id) for m in user_memberships}

    # Construire la map parent → enfants
    children_map: dict[str, list[str]] = {}
    child_ids = set()
    for h in all_hierarchies:
        parent_key = str(h.parent_team_id)
        child_key = str(h.child_team_id)
        children_map.setdefault(parent_key, []).append(child_key)
        child_ids.add(child_key)

    teams_map = {str(t.id): t for t in all_teams}

    def build_node(team_id: str) -> dict:
        team = teams_map.get(team_id)
        if not team:
            return None
        children = []
        for cid in children_map.get(team_id, []):
            node = build_node(cid)
            if node:
                children.append(node)
        return {
            "id": team_id,
            "name": team.name,
            "is_root": team.is_root,
            "is_member": team_id in user_team_ids,
            "children": children,
        }

    # Les racines de l'arbre sont les équipes qui ne sont enfant de personne
    roots = [str(t.id) for t in all_teams if str(t.id) not in child_ids]
    return [build_node(r) for r in roots if build_node(r)]


# ─── Helpers internes ─────────────────────────────────────────────

async def _assert_is_owner(user_id: PydanticObjectId, team_id: PydanticObjectId) -> None:
    """
    Vérifie que l'utilisateur est propriétaire de l'équipe,
    directement OU par héritage descendant.
    """
    # Propriétaire direct ?
    direct = await TeamMember.find_one(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user_id,
        TeamMember.role == TeamMemberRole.OWNER,
        TeamMember.status == Status.ACTIVE,
    )
    if direct:
        return

    # Propriétaire hérité ? Remonter les parents récursivement
    parent_links = await TeamHierarchy.find(
        TeamHierarchy.child_team_id == team_id
    ).to_list()

    for link in parent_links:
        # Appel récursif : si propriétaire d'un parent → propriétaire de l'enfant
        try:
            await _assert_is_owner(user_id, link.parent_team_id)
            return  # Trouvé via héritage
        except ForbiddenError:
            continue

    raise ForbiddenError("Vous n'êtes pas propriétaire de cette équipe")


async def _get_inherited_owners(team_id: PydanticObjectId) -> list[User]:
    """
    Récupère les propriétaires hérités des équipes parentes.
    Remonte récursivement toute la hiérarchie.
    """
    owners = []

    # Récupérer les équipes parentes directes
    parent_links = await TeamHierarchy.find(
        TeamHierarchy.child_team_id == team_id
    ).to_list()

    for link in parent_links:
        # Propriétaires directs de l'équipe parente
        parent_members = await TeamMember.find(
            TeamMember.team_id == link.parent_team_id,
            TeamMember.role == TeamMemberRole.OWNER,
            TeamMember.status == Status.ACTIVE,
        ).to_list()

        for member in parent_members:
            user = await User.get(member.user_id)
            if user:
                owners.append(user)

        # Remonter encore : les propriétaires des grands-parents sont aussi hérités
        owners.extend(await _get_inherited_owners(link.parent_team_id))

    return owners
