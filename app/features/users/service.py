"""
Service utilisateurs — profil et administration.
Gère la mise à jour du profil avec propagation automatique
du nom de l'organisation privée, et les actions admin.
"""

import math
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.core.audit import log_action
from app.core.enums import Status, UserRole
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.features.auth.models import User
from app.features.users.schemas import AdminUserUpdate, BulkUserItem, UserUpdate


async def update_profile(user: User, payload: UserUpdate) -> User:
    """
    Modification du profil.
    Si l'email change, vérifie son unicité.
    Si le nom/prénom change, met à jour le nom de l'organisation privée.
    """
    update_data = payload.model_dump(exclude_unset=True)

    # Vérification unicité du nouvel email
    if "email" in update_data and update_data["email"] != user.email:
        existing = await User.find_one(User.email == update_data["email"])
        if existing:
            raise ConflictError("Cet email est déjà utilisé")

    name_changed = "first_name" in update_data or "last_name" in update_data

    update_data["updated_at"] = datetime.now(timezone.utc)
    await user.set(update_data)

    # Propagation du changement de nom sur l'organisation privée
    if name_changed:
        from app.features.organizations.models import Organization

        new_name = f"{user.first_name} {user.last_name}"
        private_org = await Organization.find_one(
            Organization.owner_id == user.id,
            Organization.is_private == True,
        )
        if private_org:
            await private_org.set({
                "name": new_name,
                "updated_at": datetime.now(timezone.utc),
            })

    return user


async def get_user_by_id(user_id: str) -> User:
    """Récupère un utilisateur par son ID. Lève 404 si absent."""
    user = await User.get(PydanticObjectId(user_id))
    if not user:
        raise NotFoundError("Utilisateur non trouvé")
    return user


async def admin_update_user(
    admin: User,
    target_user_id: str,
    payload: AdminUserUpdate,
) -> User:
    """
    Modification du statut ou du rôle par un admin.

    Règles métier :
      - Un admin ne peut pas se désactiver lui-même
      - Le dernier admin ne peut pas se retirer le rôle admin
    """
    target = await get_user_by_id(target_user_id)
    update_data = payload.model_dump(exclude_unset=True)

    # Protection : un admin ne peut pas se désactiver lui-même
    if "status" in update_data and str(target.id) == str(admin.id):
        if update_data["status"] == Status.INACTIVE:
            raise ForbiddenError("Vous ne pouvez pas vous désactiver vous-même")

    # Protection : le dernier admin ne peut pas se retirer son rôle
    if "role" in update_data and update_data["role"] == UserRole.USER:
        if target.role == UserRole.ADMIN:
            admin_count = await User.find(User.role == UserRole.ADMIN).count()
            if admin_count <= 1:
                raise ForbiddenError(
                    "Impossible : il doit rester au moins un administrateur"
                )

    update_data["updated_at"] = datetime.now(timezone.utc)
    await target.set(update_data)

    # Journalisation des changements sensibles
    if "status" in payload.model_dump(exclude_unset=True):
        await log_action(
            admin.id, "USER_STATUS_CHANGE",
            f"User {target.email} status → {update_data.get('status')}",
        )
    if "role" in payload.model_dump(exclude_unset=True):
        await log_action(
            admin.id, "USER_ROLE_CHANGE",
            f"User {target.email} role → {update_data.get('role')}",
        )

    return target


async def list_users(
    search: str | None,
    status: int | None,
    role: int | None,
    page: int,
    page_size: int,
) -> dict:
    """
    Liste paginée des utilisateurs avec filtres.
    Retourne { items, total, page, page_size, total_pages }.
    """
    query = User.find()

    # Filtres optionnels
    if status is not None:
        query = query.find(User.status == status)
    if role is not None:
        query = query.find(User.role == role)
    if search:
        # Recherche insensible à la casse sur nom, prénom ou email
        import re
        pattern = re.compile(re.escape(search), re.IGNORECASE)
        query = query.find(
            {"$or": [
                {"first_name": {"$regex": pattern}},
                {"last_name": {"$regex": pattern}},
                {"email": {"$regex": pattern}},
            ]}
        )

    total = await query.count()
    total_pages = max(1, math.ceil(total / page_size))

    # Pagination : skip/limit avec tri par date de création
    skip = (page - 1) * page_size
    users = await query.skip(skip).limit(page_size).sort("-created_at").to_list()

    return {
        "items": users,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def bulk_create_users(
    admin: User,
    items: list[BulkUserItem],
) -> list[dict]:
    """
    Création massive de comptes utilisateurs par un administrateur.

    Pour chaque utilisateur :
      1. Valide le mot de passe (politique de complexité)
      2. Crée le compte (statut Actif, rôle Utilisateur)
      3. Crée l'organisation privée "Prénom Nom"
      4. Ajoute l'utilisateur comme membre de l'équipe racine
         de chaque organisation spécifiée dans organization_ids

    Les erreurs sur un utilisateur n'empêchent pas la création des suivants
    (approche transactionnelle par item, pas globale).
    """
    from app.core.security import hash_password
    from app.core.validators import validate_password
    from app.features.organizations.models import Organization
    from app.features.organizations.service import create_private_organization
    from app.features.teams.models import Team, TeamMember

    results = []

    for item in items:
        try:
            # Validation du mot de passe
            validate_password(item.password)

            # Vérification unicité email
            existing = await User.find_one(User.email == item.email)
            if existing:
                results.append({
                    "email": item.email,
                    "success": False,
                    "user_id": None,
                    "error": "Email déjà utilisé",
                })
                continue

            # Création du compte
            user = User(
                email=item.email,
                hashed_password=hash_password(item.password),
                first_name=item.first_name,
                last_name=item.last_name,
                role=UserRole.USER,
                status=Status.ACTIVE,
            )
            await user.insert()

            # Création de l'organisation privée (comme à l'inscription)
            await create_private_organization(user)

            # Ajout comme membre de chaque organisation demandée :
            # on retrouve l'équipe racine de l'org et on y ajoute l'utilisateur
            for org_id in item.organization_ids:
                org = await Organization.get(PydanticObjectId(org_id))
                if not org:
                    continue  # Organisation introuvable, on skip silencieusement

                root_team = await Team.find_one(
                    Team.organization_id == org.id,
                    Team.is_root == True,
                )
                if not root_team:
                    continue

                # Éviter les doublons si l'utilisateur est déjà membre
                already = await TeamMember.find_one(
                    TeamMember.team_id == root_team.id,
                    TeamMember.user_id == user.id,
                )
                if not already:
                    from app.core.enums import TeamMemberRole
                    membership = TeamMember(
                        team_id=root_team.id,
                        user_id=user.id,
                        role=TeamMemberRole.MEMBER,
                        status=Status.ACTIVE,
                    )
                    await membership.insert()

            results.append({
                "email": item.email,
                "success": True,
                "user_id": str(user.id),
                "error": None,
            })

            await log_action(
                admin.id, "BULK_USER_CREATE",
                f"User {item.email} created by admin bulk import",
            )

        except Exception as e:
            # Capture toute erreur inattendue sans bloquer le reste du batch
            results.append({
                "email": item.email,
                "success": False,
                "user_id": None,
                "error": str(e),
            })

    return results
