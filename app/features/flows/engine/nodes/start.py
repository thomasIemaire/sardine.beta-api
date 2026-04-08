"""Nœud start — point d'entrée du flow."""

from ..context import ExecutionContext, NodeResult


async def execute_start(node: dict, context: ExecutionContext, engine) -> NodeResult:
    """
    Si dans une boucle, extrait l'item courant (ex: un fichier).
    Sinon, extrait le premier fichier de context.data["files"] s'il existe.
    """
    item = context.variables.get("item")
    if isinstance(item, dict) and item.get("base64"):
        context.data["fileBase64"] = item["base64"]
        context.data["fileName"] = item.get("name", "")
        context.data["fileMimeType"] = item.get("mime_type", "")
        context.data["fileSize"] = item.get("size", 0)
    else:
        files = context.data.get("files")
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, dict) and first.get("base64"):
                context.data["fileBase64"] = first["base64"]
                context.data["fileName"] = first.get("name", "")
                context.data["fileMimeType"] = first.get("mime_type", "")
                context.data["fileSize"] = first.get("size", 0)

    return NodeResult(output_port=0)
