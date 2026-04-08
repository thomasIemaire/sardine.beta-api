"""Nœud merge — point de jonction (pass-through)."""

from ..context import ExecutionContext, NodeResult


async def execute_merge(node: dict, context: ExecutionContext, engine) -> NodeResult:
    return NodeResult(output_port=0)
