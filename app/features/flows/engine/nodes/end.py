"""Nœud end — termine l'exécution du flow."""

from ..context import ExecutionContext, NodeResult
from ..expressions import set_value


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusionne override dans base récursivement. Les dicts sont mergés, les autres valeurs écrasées."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


async def execute_end(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    status = config.get("status", "completed")
    if status == "failed":
        return NodeResult(error="Flow terminé avec statut 'failed' par le nœud end")

    # Fusionner tous les résultats des nœuds agents en un seul dict agentResults
    extractions = context.data.get("agentExtractions")
    if isinstance(extractions, list) and extractions:
        merged: dict = {}
        for entry in extractions:
            fields = entry.get("fields")
            if isinstance(fields, dict):
                merged = _deep_merge(merged, fields)
        set_value(context.data, "agentResults", merged)

    return NodeResult(output_port=0)
