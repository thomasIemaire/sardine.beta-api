"""Moteur d'exécution de flows.

Porté depuis sardine.api/services/flow_engine.
Fournit FlowEngine, ExecutionContext et un registre global des tâches en cours.
"""

import asyncio

from .context import ExecutionContext, LoopFrame, NodeResult
from .engine import FlowEngine

__all__ = ["FlowEngine", "ExecutionContext", "NodeResult", "LoopFrame"]


# ─── Registre global : execution_id → asyncio.Task ───────────────

_execution_tasks: dict[str, asyncio.Task] = {}


def register_execution(execution_id: str, task: asyncio.Task) -> None:
    _execution_tasks[execution_id] = task


def unregister_execution(execution_id: str) -> None:
    _execution_tasks.pop(execution_id, None)


def cancel_execution(execution_id: str) -> bool:
    """Annule une exécution en cours. Retourne True si annulée."""
    task = _execution_tasks.pop(execution_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False
