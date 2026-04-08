"""Nœud edit — transformation de données (set / delete / rename)."""

from ..context import ExecutionContext, NodeResult
from ..expressions import delete_value, get_value, resolve_template, set_value


def _parse_value(s: str):
    """Tente de parser une string en number/bool/None, sinon retourne la string."""
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


async def execute_edit(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    operations = config.get("operations", [])

    for op in operations:
        op_type = op.get("type")
        path = op.get("path", "")

        if op_type == "set":
            raw_value = op.get("value", "")
            resolved = resolve_template(str(raw_value), context)
            value = _parse_value(resolved)
            set_value(context.data, path, value)

        elif op_type == "delete":
            delete_value(context.data, path)

        elif op_type == "rename":
            new_path = op.get("newPath", "")
            if not new_path:
                return NodeResult(error=f"EDIT rename: 'newPath' requis pour '{path}'")
            val = get_value(context.data, path)
            if val is None:
                return NodeResult(error=f"EDIT rename: chemin '{path}' introuvable")
            set_value(context.data, new_path, val)
            delete_value(context.data, path)

        else:
            return NodeResult(error=f"EDIT: type d'opération inconnu '{op_type}'")

    return NodeResult(
        output_port=0,
        metadata={
            "operations_count": len(operations),
            "operations": [op.get("type") for op in operations],
        },
    )
