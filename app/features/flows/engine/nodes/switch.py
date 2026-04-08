"""Nœud switch — branchement multi-voies."""

from ..context import ExecutionContext, NodeResult
from ..expressions import get_value


async def execute_switch(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    field = config.get("field")
    cases = config.get("cases", [])

    if not field:
        return NodeResult(error="Le nœud SWITCH requiert un champ 'field'")

    actual = get_value(context.data, field)
    actual_str = str(actual) if actual is not None else ""

    for i, case in enumerate(cases):
        if str(case.get("value", "")) == actual_str:
            return NodeResult(
                output_port=i,
                metadata={
                    "field": field, "actual": actual_str,
                    "matched_case": case.get("value"), "matched_port": i,
                },
            )

    return NodeResult(error=f"SWITCH: aucun case ne correspond à '{actual_str}'")
