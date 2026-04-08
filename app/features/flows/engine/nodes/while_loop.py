"""Nœud while — boucle tant que la condition est vraie."""

from ..context import ExecutionContext, LoopFrame, NodeResult
from ..expressions import evaluate


async def execute_while(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    condition = config.get("condition")
    max_iter = config.get("maxIterations", 1000)

    if not condition:
        return NodeResult(error="WHILE: 'condition' requise")

    frame = LoopFrame(
        loop_node_id=node["id"],
        loop_type="while",
        max_iterations=max_iter,
    )
    context.loop_stack.append(frame)

    iteration = 0
    while evaluate(condition, context):
        if iteration >= max_iter:
            context.loop_stack.pop()
            return NodeResult(error=f"WHILE: limite de {max_iter} itérations atteinte")
        context.variables["iteration"] = iteration
        await engine._run_children(node["id"], context)
        iteration += 1
        frame.iteration_count = iteration

    context.loop_stack.pop()
    context.variables.pop("iteration", None)
    return NodeResult(
        output_port=0,
        metadata={"condition": condition, "iterations": iteration},
    )
