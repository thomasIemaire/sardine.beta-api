"""
Initialisation de la connexion MongoDB et de Beanie.
Tous les document models sont enregistrés ici pour que Beanie
puisse créer les index et gérer les collections automatiquement.
"""

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings

client = AsyncIOMotorClient(settings.MONGODB_URL)
db = client[settings.MONGODB_NAME]


async def init_db() -> None:
    """
    Initialise Beanie avec tous les document models de l'application.
    Appelée au démarrage via le lifespan de FastAPI.
    """
    # Import de tous les models pour que Beanie les enregistre
    from app.features.api_keys.models import ApiKey
    from app.features.agents.models import Agent, AgentFieldFeedback, AgentShare, AgentVersion
    from app.features.audit.models import AuditLog
    from app.features.auth.models import TokenBlacklist, User
    from app.features.files.comments import FileComment
    from app.features.files.models import File, FileVersion
    from app.features.files.tags import Tag
    from app.features.flows.models import (
        ApprovalTask,
        ExecutionNodeLog,
        Flow,
        FlowExecution,
        FlowShare,
        FlowVersion,
    )
    from app.features.folders.models import Folder
    from app.features.notifications.models import Notification
    from app.features.organizations.models import Organization
    from app.features.permissions.models import FolderMemberPermission, FolderTeamPermission
    from app.features.teams.models import Team, TeamHierarchy, TeamMember

    await init_beanie(
        database=db,
        document_models=[
            User,
            TokenBlacklist,
            Organization,
            Folder,
            Team,
            TeamMember,
            TeamHierarchy,
            AuditLog,
            Notification,
            Agent,
            AgentVersion,
            AgentShare,
            AgentFieldFeedback,
            Flow,
            FlowVersion,
            FlowShare,
            FlowExecution,
            ExecutionNodeLog,
            ApprovalTask,
            File,
            FileVersion,
            FileComment,
            Tag,
            FolderTeamPermission,
            FolderMemberPermission,
            ApiKey,
        ],
    )
