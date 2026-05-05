"""
Schemas Pydantic pour les serveurs et leurs statistiques.
Le format de ServerStats reflète exactement la réponse /stats du serveur GPU.
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.core.enums import (
    SERVER_HEALTH_LABELS,
    SERVER_TYPE_LABELS,
    ServerHealth,
    ServerType,
)

# ─── CRUD serveur ────────────────────────────────────────────────


class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: ServerType
    base_url: HttpUrl
    api_key: str = ""
    enabled: bool = True
    description: str = ""


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: HttpUrl | None = None
    api_key: str | None = None
    enabled: bool | None = None
    description: str | None = None


class ServerRead(BaseModel):
    id: str
    name: str
    type: ServerType
    type_label: str
    base_url: str
    has_api_key: bool
    enabled: bool
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, server) -> "ServerRead":
        return cls(
            id=str(server.id),
            name=server.name,
            type=server.type,
            type_label=SERVER_TYPE_LABELS.get(server.type, server.type),
            base_url=server.base_url,
            has_api_key=bool(server.api_key),
            enabled=server.enabled,
            description=server.description,
            created_at=server.created_at,
            updated_at=server.updated_at,
        )


# ─── Statistiques live ────────────────────────────────────────────
# Reflètent la réponse /stats du serveur GPU. Tous les champs internes
# sont optionnels pour rester compatible avec d'autres types de serveurs.


class GpuStat(BaseModel):
    index: int
    name: str
    driver_version: str | None = None
    temperature_c: float | None = None
    gpu_util_pct: float | None = None
    memory_util_pct: float | None = None
    vram_total_mb: float | None = None
    vram_used_mb: float | None = None
    vram_free_mb: float | None = None
    power_draw_w: float | None = None
    power_limit_w: float | None = None
    fan_speed_pct: float | None = None
    clock_graphics_mhz: float | None = None
    clock_memory_mhz: float | None = None
    vram_allocated_mb: float | None = None
    vram_reserved_mb: float | None = None


class CpuStat(BaseModel):
    count_logical: int | None = None
    count_physical: int | None = None
    percent: float | None = None
    load_avg_1m: float | None = None


class MemoryStat(BaseModel):
    total_gb: float | None = None
    available_gb: float | None = None
    used_gb: float | None = None
    percent: float | None = None


class SwapStat(BaseModel):
    total_gb: float | None = None
    used_gb: float | None = None
    percent: float | None = None


class DiskStat(BaseModel):
    total_gb: float | None = None
    used_gb: float | None = None
    free_gb: float | None = None
    percent: float | None = None


class SystemStat(BaseModel):
    cpu: CpuStat | None = None
    memory: MemoryStat | None = None
    swap: SwapStat | None = None
    disk: DiskStat | None = None


class ProcessStat(BaseModel):
    pid: int | None = None
    memory_rss_mb: float | None = None
    memory_vms_mb: float | None = None
    cpu_percent: float | None = None
    num_threads: int | None = None
    create_time: float | None = None


class VllmStat(BaseModel):
    status: str | None = None
    url: str | None = None
    models: list[str] = Field(default_factory=list)


class QueueStat(BaseModel):
    available: int
    max: int


class PlatformStat(BaseModel):
    system: str | None = None
    release: str | None = None
    python: str | None = None
    machine: str | None = None


class ServerStats(BaseModel):
    """Payload retourné par GET /api/servers/{id}/stats."""

    server_id: str
    server_name: str
    server_type: ServerType
    health: ServerHealth
    health_label: str
    fetched_at: datetime
    latency_ms: float | None = None
    error: str | None = None  # Renseigné si health != OK

    # Champs renvoyés par /stats du serveur — tous optionnels car ils
    # peuvent manquer si le serveur est UNREACHABLE.
    status: str | None = None
    uptime_s: float | None = None
    device: str | None = None
    platform: PlatformStat | None = None
    gpus: list[GpuStat] = Field(default_factory=list)
    system: SystemStat | None = None
    process: ProcessStat | None = None
    vllm: VllmStat | None = None
    queues: dict[str, QueueStat] = Field(default_factory=dict)

    @classmethod
    def unreachable(
        cls,
        server,
        error: str,
        fetched_at: datetime,
        health: ServerHealth = ServerHealth.UNREACHABLE,
    ) -> "ServerStats":
        return cls(
            server_id=str(server.id),
            server_name=server.name,
            server_type=server.type,
            health=health,
            health_label=SERVER_HEALTH_LABELS[health],
            fetched_at=fetched_at,
            error=error,
        )

    @classmethod
    def from_payload(
        cls,
        server,
        payload: dict,
        fetched_at: datetime,
        latency_ms: float,
    ) -> "ServerStats":
        return cls(
            server_id=str(server.id),
            server_name=server.name,
            server_type=server.type,
            health=ServerHealth.OK,
            health_label=SERVER_HEALTH_LABELS[ServerHealth.OK],
            fetched_at=fetched_at,
            latency_ms=latency_ms,
            status=payload.get("status"),
            uptime_s=payload.get("uptime_s"),
            device=payload.get("device"),
            platform=payload.get("platform"),
            gpus=payload.get("gpus") or [],
            system=payload.get("system"),
            process=payload.get("process"),
            vllm=payload.get("vllm"),
            queues=payload.get("queues") or {},
        )
