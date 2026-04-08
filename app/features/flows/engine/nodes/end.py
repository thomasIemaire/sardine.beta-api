"""Nœud end — termine l'exécution du flow."""

from ..context import ExecutionContext, NodeResult


async def execute_end(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    status = config.get("status", "completed")
    if status == "failed":
        return NodeResult(error="Flow terminé avec statut 'failed' par le nœud end")
    return NodeResult(output_port=0)
