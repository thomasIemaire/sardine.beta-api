"""
Endpoints de gestion et de monitoring des serveurs.
Tous réservés aux administrateurs (UserRole.ADMIN).
"""

from fastapi import APIRouter, status

from app.features.auth.dependencies import CurrentAdmin
from app.features.servers.schemas import (
    ServerCreate,
    ServerRead,
    ServerStats,
    ServerUpdate,
)
from app.features.servers.service import (
    _get_server_or_404,
    create_server,
    delete_server,
    fetch_all_stats,
    fetch_server_stats,
    list_servers,
    update_server,
)

router = APIRouter(prefix="/servers", tags=["Servers"])


@router.get("/", response_model=list[ServerRead])
async def list_all(current_admin: CurrentAdmin):
    """Liste tous les serveurs configurés."""
    servers = await list_servers()
    return [ServerRead.from_model(s) for s in servers]


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=ServerRead)
async def create(payload: ServerCreate, current_admin: CurrentAdmin):
    """Ajoute un nouveau serveur monitorable."""
    server = await create_server(current_admin, payload)
    return ServerRead.from_model(server)


@router.patch("/{server_id}", response_model=ServerRead)
async def update(server_id: str, payload: ServerUpdate, current_admin: CurrentAdmin):
    """Modifie un serveur (URL, clé, statut activé, description, nom)."""
    server = await update_server(current_admin, server_id, payload)
    return ServerRead.from_model(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(server_id: str, current_admin: CurrentAdmin):
    """Supprime un serveur de la liste monitorée."""
    await delete_server(current_admin, server_id)


@router.get("/stats", response_model=list[ServerStats])
async def stats_all(current_admin: CurrentAdmin):
    """Stats live de tous les serveurs en parallèle."""
    return await fetch_all_stats()


@router.get("/{server_id}/stats", response_model=ServerStats)
async def stats_one(server_id: str, current_admin: CurrentAdmin):
    """Stats live d'un serveur (cache court ~2s pour éviter de spammer le serveur cible)."""
    server = await _get_server_or_404(server_id)
    return await fetch_server_stats(server)
