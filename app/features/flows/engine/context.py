"""Structures de données pour l'exécution d'un flow."""

from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class NodeResult:
    """Retour standard d'un handler de nœud."""
    output_port: int = 0
    error: str | None = None
    pause: bool = False
    metadata: dict | None = None


@dataclass
class LoopFrame:
    """État d'une boucle en cours d'exécution."""
    loop_node_id: str
    loop_type: str  # "for", "while", "do_while"
    items: list = field(default_factory=list)
    index: int = 0
    max_iterations: int = 1000
    iteration_count: int = 0


@dataclass
class ExecutionContext:
    """Contexte mutable transmis entre les nœuds."""
    data: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    loop_stack: list = field(default_factory=list)

    def clone(self) -> "ExecutionContext":
        return ExecutionContext(
            data=deepcopy(self.data),
            variables=deepcopy(self.variables),
            metadata=deepcopy(self.metadata),
            loop_stack=deepcopy(self.loop_stack),
        )
