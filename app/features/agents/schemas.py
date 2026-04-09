"""
Schémas Pydantic pour les agents, versions et partage.
"""

from datetime import datetime

from pydantic import BaseModel

# ─── Requêtes ────────────────────────────────────────────────────

class AgentCreate(BaseModel):
    """Création d'un agent avec son schéma initial (1ère version)."""

    name: str
    description: str = ""
    schema_data: dict  # Schéma JSON de la version initiale


class AgentUpdate(BaseModel):
    """Modification du nom et/ou de la description d'un agent."""

    name: str | None = None
    description: str | None = None


class AgentVersionCreate(BaseModel):
    """
    Création d'une nouvelle version.
    Si parent_version_id est omis, branche depuis la version active.
    """

    schema_data: dict
    parent_version_id: str | None = None


class ActiveVersionUpdate(BaseModel):
    """Changement de la version active (checkout)."""

    version_id: str


class AgentShareCreate(BaseModel):
    """Partage d'un agent avec une ou plusieurs organisations (lecture seule)."""

    target_org_ids: list[str]


# ─── Réponses ────────────────────────────────────────────────────

class AgentVersionRead(BaseModel):
    """Lecture d'une version d'agent."""

    id: str
    agent_id: str
    schema_data: dict
    parent_version_id: str | None
    created_by: str
    created_by_name: str | None = None
    created_at: datetime

    @classmethod
    def from_version(
        cls, version, creator_name: str | None = None,
    ) -> "AgentVersionRead":
        return cls(
            id=str(version.id),
            agent_id=str(version.agent_id),
            schema_data=version.schema_data,
            parent_version_id=(
                str(version.parent_version_id) if version.parent_version_id else None
            ),
            created_by=str(version.created_by),
            created_by_name=creator_name,
            created_at=version.created_at,
        )


class AgentRead(BaseModel):
    """Lecture d'un agent avec optionnellement le schéma de la version active."""

    id: str
    name: str
    description: str
    organization_id: str
    active_version_id: str | None
    active_version_schema: dict | None = None
    forked_from_id: str | None = None
    forked_from_version_id: str | None = None
    created_by: str
    created_by_name: str | None = None
    created_at: datetime
    used_in_flows: bool = False

    @classmethod
    def from_agent(
        cls, agent, active_schema: dict | None = None,
        creator_name: str | None = None,
        used_in_flows: bool = False,
    ) -> "AgentRead":
        return cls(
            id=str(agent.id),
            name=agent.name,
            description=agent.description,
            organization_id=str(agent.organization_id),
            active_version_id=(
                str(agent.active_version_id) if agent.active_version_id else None
            ),
            active_version_schema=active_schema,
            forked_from_id=(
                str(agent.forked_from_id) if agent.forked_from_id else None
            ),
            forked_from_version_id=(
                str(agent.forked_from_version_id)
                if agent.forked_from_version_id else None
            ),
            created_by=str(agent.created_by),
            created_by_name=creator_name,
            created_at=agent.created_at,
            used_in_flows=used_in_flows,
        )


class AgentShareRead(BaseModel):
    """Lecture d'un partage d'agent."""

    id: str
    agent_id: str
    shared_with_org_id: str
    shared_by: str
    created_at: datetime

    @classmethod
    def from_share(cls, share) -> "AgentShareRead":
        return cls(
            id=str(share.id),
            agent_id=str(share.agent_id),
            shared_with_org_id=str(share.shared_with_org_id),
            shared_by=str(share.shared_by),
            created_at=share.created_at,
        )
