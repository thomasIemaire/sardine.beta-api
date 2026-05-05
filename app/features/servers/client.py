"""
Client HTTP pour récupérer les statistiques d'un serveur.
Appelle GET {base_url}/stats avec un timeout court pour ne pas bloquer
l'UI si le serveur ne répond pas.
"""

import httpx

from app.features.servers.models import Server

STATS_TIMEOUT_SECONDS = 5.0


def _headers(server: Server) -> dict:
    h: dict[str, str] = {}
    if server.api_key:
        h["x-api-key"] = server.api_key
    return h


async def fetch_stats_payload(server: Server) -> dict:
    """
    Appelle GET /stats sur le serveur. Lève httpx.HTTPError ou httpx.TimeoutException
    en cas d'échec — le service appelant les transforme en réponse UNREACHABLE.
    """
    url = f"{server.base_url.rstrip('/')}/stats"
    async with httpx.AsyncClient(timeout=STATS_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=_headers(server))
        response.raise_for_status()
        return response.json()
