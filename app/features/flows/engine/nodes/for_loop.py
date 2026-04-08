"""Nœud for — itère sur une liste."""

from ..context import ExecutionContext, LoopFrame, NodeResult
from ..expressions import get_value


async def execute_for(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    iterable_field = config.get("iterableField")

    if not iterable_field:
        return NodeResult(error="FOR: 'iterableField' requis")

    items = get_value(context.data, iterable_field)
    if items is None:
        items = []
    if not isinstance(items, list):
        return NodeResult(error=f"FOR: '{iterable_field}' n'est pas une liste")

    if not items:
        return NodeResult(output_port=0)

    execution_id = context.metadata.get("execution_id")
    org_id = context.metadata.get("org_id")

    frame = LoopFrame(
        loop_node_id=node["id"],
        loop_type="for",
        items=items,
        index=0,
        max_iterations=len(items),
    )
    context.loop_stack.append(frame)

    for i, item in enumerate(items):
        frame.index = i
        frame.iteration_count = i
        context.variables["item"] = item
        context.variables["index"] = i
        context.variables["length"] = len(items)

        await engine._emit_loop_event(
            execution_id, org_id, node["id"], i, len(items), "started",
        )
        await engine._run_children(node["id"], context)
        await engine._emit_loop_event(
            execution_id, org_id, node["id"], i, len(items), "completed",
        )

    context.loop_stack.pop()
    context.variables.pop("item", None)
    context.variables.pop("index", None)
    context.variables.pop("length", None)

    return NodeResult(
        output_port=0,
        metadata={"iterable_field": iterable_field, "items_count": len(items)},
    )
