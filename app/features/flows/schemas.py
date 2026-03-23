"""
Schémas Pydantic pour les flows, versions et partage.
"""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import FLOW_STATUS_LABELS

# ─── Requêtes ────────────────────────────────────────────────────

class FlowCreate(BaseModel):
    """Création d'un flow avec ses données initiales (1ère version)."""

    name: str
    description: str = ""
    flow_data: dict  # Données JSON de la version initiale


class FlowUpdate(BaseModel):
    """Modification du nom, de la description et/ou du statut d'un flow."""

    name: str | None = None
    description: str | None = None
    status: str | None = None


class FlowVersionCreate(BaseModel):
    """
    Création d'une nouvelle version.
    Si parent_version_id est omis, branche depuis la version active.
    """

    flow_data: dict
    parent_version_id: str | None = None


class ActiveVersionUpdate(BaseModel):
    """Changement de la version active (checkout)."""

    version_id: str


class FlowShareCreate(BaseModel):
    """Partage d'un flow avec une ou plusieurs organisations (lecture seule)."""

    target_org_ids: list[str]


# ─── Réponses ────────────────────────────────────────────────────

class FlowVersionRead(BaseModel):
    """Lecture d'une version de flow."""

    id: str
    flow_id: str
    flow_data: dict
    parent_version_id: str | None
    created_by: str
    created_at: datetime

    @classmethod
    def from_version(cls, version) -> "FlowVersionRead":
        return cls(
            id=str(version.id),
            flow_id=str(version.flow_id),
            flow_data=version.flow_data,
            parent_version_id=(
                str(version.parent_version_id) if version.parent_version_id else None
            ),
            created_by=str(version.created_by),
            created_at=version.created_at,
        )


class FlowRead(BaseModel):
    """Lecture d'un flow avec optionnellement les données de la version active."""

    id: str
    name: str
    description: str
    organization_id: str
    status: str
    status_label: str
    active_version_id: str | None
    active_version_data: dict | None = None
    forked_from_id: str | None = None
    forked_from_version_id: str | None = None
    created_by: str
    created_at: datetime

    @classmethod
    def from_flow(
        cls, flow, active_data: dict | None = None,
    ) -> "FlowRead":
        return cls(
            id=str(flow.id),
            name=flow.name,
            description=flow.description,
            organization_id=str(flow.organization_id),
            status=flow.status,
            status_label=FLOW_STATUS_LABELS.get(flow.status, flow.status),
            active_version_id=(
                str(flow.active_version_id) if flow.active_version_id else None
            ),
            active_version_data=active_data,
            forked_from_id=(
                str(flow.forked_from_id) if flow.forked_from_id else None
            ),
            forked_from_version_id=(
                str(flow.forked_from_version_id)
                if flow.forked_from_version_id else None
            ),
            created_by=str(flow.created_by),
            created_at=flow.created_at,
        )


class FlowShareRead(BaseModel):
    """Lecture d'un partage de flow."""

    id: str
    flow_id: str
    shared_with_org_id: str
    shared_by: str
    created_at: datetime

    @classmethod
    def from_share(cls, share) -> "FlowShareRead":
        return cls(
            id=str(share.id),
            flow_id=str(share.flow_id),
            shared_with_org_id=str(share.shared_with_org_id),
            shared_by=str(share.shared_by),
            created_at=share.created_at,
        )
