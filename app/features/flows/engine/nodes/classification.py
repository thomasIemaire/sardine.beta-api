"""
Nœud classification — classifie un document via le serveur GPU Sardine.

Config attendu :
  {
    "documentClasses": ["facture", "contrat", ...],
    "modelRepo": "Sendoc/sard-cls",       // optionnel
    "modelFilename": "best.pt"            // optionnel
  }

2 ports : 0 = "Valide" (top label correspond), 1 = "Invalide".
Lit context.data["fileBase64"], écrit context.data["classificationResult"].
"""

from ..context import ExecutionContext, NodeResult
from ..expressions import set_value
from . import gpu_client


# Mapping labels modèle (EN) → valeurs documentClass front (FR)
LABEL_TO_CLASS = {
    "invoice": "facture",
    "payslip": "bulletin-de-paie",
    "contract": "contrat",
    "quote": "devis",
    "purchase_order": "bon_de_commande",
    "credit_note": "avoir",
    "bank_statement": "releve_bancaire",
    "certificate": "attestation",
}


async def execute_classification(
    node: dict, context: ExecutionContext, engine,
) -> NodeResult:
    config = node.get("config", {})

    document_classes = config.get("documentClasses", [])
    if not document_classes:
        return NodeResult(error="Classification: aucune classe de document configurée")

    model_repo = config.get("modelRepo", "Sendoc/sard-cls")
    model_filename = config.get("modelFilename", "best.pt")

    b64_value = context.data.get("fileBase64")
    if not b64_value or not isinstance(b64_value, str):
        return NodeResult(error="Classification: champ 'fileBase64' manquant dans context.data")

    # Appel du serveur GPU
    try:
        gpu_resp = await gpu_client.classify(b64_value, model_repo, model_filename)
    except Exception as exc:
        return NodeResult(error=f"Classification: erreur serveur GPU — {exc}")

    top_label = gpu_resp.get("topLabel")
    pages_data = []
    for p in gpu_resp.get("pages", []):
        pages_data.append({
            "page": p.get("page"),
            "topLabel": p.get("topLabel"),
        })

    # Mapping label → classe front
    mapped_class = (
        LABEL_TO_CLASS.get(top_label.lower().strip(), top_label)
        if top_label else None
    )
    document_classes_lower = [c.lower().strip() for c in document_classes]
    is_valid = (
        mapped_class is not None
        and mapped_class.lower().strip() in document_classes_lower
    )

    result_data = {
        "modelId": model_repo,
        "topLabel": top_label,
        "mappedClass": mapped_class,
        "isValid": is_valid,
        "documentClasses": document_classes,
        "pages": pages_data,
    }
    set_value(context.data, "classificationResult", result_data)

    output_port = 0 if is_valid else 1

    return NodeResult(
        output_port=output_port,
        metadata={
            "model": model_repo,
            "top_label": top_label,
            "mapped_class": mapped_class,
            "is_valid": is_valid,
            "document_classes": document_classes,
            "pages_count": len(pages_data),
        },
    )
