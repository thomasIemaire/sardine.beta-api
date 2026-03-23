"""
Routes journal d'audit (US-FILE-12).
Lecture seule — accessible uniquement par le proprietaire de l'organisation.
"""

from fastapi import APIRouter, Query

from app.features.audit.schemas import AuditLogRead
from app.features.audit.service import get_audit_actions, list_audit_logs
from app.features.auth.dependencies import CurrentUser

router = APIRouter(
    prefix="/organizations/{org_id}/audit-logs",
    tags=["Audit"],
)


@router.get("/")
async def get_audit_logs(
    org_id: str,
    current_user: CurrentUser,
    action: str | None = Query(None),
    user_id: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    """
    Journal d'audit de l'organisation (pagine, filtrable).
    Filtres : action, user_id, search (dans les details).
    Proprietaire de l'organisation uniquement.
    """
    result = await list_audit_logs(
        current_user, org_id,
        action=action,
        user_id_filter=user_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [AuditLogRead.from_log(log) for log in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


@router.get("/actions", response_model=list[str])
async def get_actions(org_id: str, current_user: CurrentUser):
    """Liste des types d'actions distincts (pour le filtre front)."""
    return await get_audit_actions(current_user, org_id)
