"""
Nœud determination — détecte des zones dans un document via le serveur GPU.

Config attendu :
  {
    "modelRepo": "Sendoc/sard-det",      // optionnel
    "modelFilename": "best.pt",          // optionnel
    "confidenceThreshold": 0.5           // optionnel
  }

1 port. Lit context.data["fileBase64"], écrit context.data["determinationResult"].
"""

from ..context import ExecutionContext, NodeResult
from ..expressions import set_value
from . import gpu_client


async def execute_determination(
    node: dict, context: ExecutionContext, engine,
) -> NodeResult:
    config = node.get("config", {})

    model_repo = config.get("modelRepo", "Sendoc/sard-det")
    model_filename = config.get("modelFilename", "best.pt")
    conf_threshold = config.get("confidenceThreshold", 0.5)

    b64_value = context.data.get("fileBase64")
    if not b64_value or not isinstance(b64_value, str):
        return NodeResult(error="Determination: champ 'fileBase64' manquant dans context.data")

    try:
        gpu_resp = await gpu_client.detect(
            b64_value, model_repo, model_filename, conf_threshold,
        )
    except Exception as exc:
        return NodeResult(error=f"Determination: erreur serveur GPU — {exc}")

    result_data = gpu_resp.get("determinationResult", {})
    set_value(context.data, "determinationResult", result_data)

    return NodeResult(
        output_port=gpu_resp.get("output_port", 0),
        metadata=gpu_resp.get("metadata", {}),
    )
