"""Nœud if — branchement conditionnel (port 0=true, port 1=false)."""

from ..context import ExecutionContext, NodeResult
from ..expressions import evaluate, get_value


async def execute_if(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})

    # Mode avancé : expression
    condition_expr = config.get("condition")
    if condition_expr:
        result = evaluate(condition_expr, context)
        return NodeResult(
            output_port=0 if result else 1,
            metadata={"mode": "expression", "condition": condition_expr, "result": bool(result)},
        )

    # Mode simple : field + operator + value
    field = config.get("field")
    operator = config.get("operator")
    value = config.get("value")

    if not field or not operator:
        return NodeResult(error="Le nœud IF requiert 'condition' ou 'field'+'operator'")

    actual = get_value(context.data, field)

    if operator == "equals":
        matched = str(actual) == str(value)
    elif operator == "contains":
        if isinstance(actual, str):
            matched = str(value) in actual
        elif isinstance(actual, list):
            matched = value in actual
        else:
            matched = False
    elif operator == "greater":
        try:
            matched = float(actual) > float(value)
        except (TypeError, ValueError):
            matched = False
    elif operator == "less":
        try:
            matched = float(actual) < float(value)
        except (TypeError, ValueError):
            matched = False
    else:
        return NodeResult(error=f"Opérateur IF inconnu: {operator}")

    return NodeResult(
        output_port=0 if matched else 1,
        metadata={
            "mode": "simple", "field": field, "operator": operator,
            "expected": value, "actual": str(actual), "result": matched,
        },
    )
