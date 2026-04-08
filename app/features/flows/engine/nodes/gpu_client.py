"""
Client HTTP pour le serveur GPU Sardine.
Endpoints supportés :
  - POST /classify  (classification de documents)
  - POST /detect    (détection de zones)
  - POST /extract   (extraction LLM)
"""

import httpx

from app.config import settings


def _base_url() -> str:
    url = (settings.GPU_API_BASE_URL or "").rstrip("/")
    if not url:
        raise RuntimeError("GPU_API_BASE_URL n'est pas configuré dans .env")
    return url


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.GPU_API_KEY:
        h["x-api-key"] = settings.GPU_API_KEY
    return h


async def classify(
    file_base64: str,
    model_repo: str = "Sendoc/sard-cls",
    model_filename: str = "best.pt",
) -> dict:
    """Appelle POST /classify et retourne la réponse JSON."""
    async with httpx.AsyncClient(timeout=settings.GPU_API_TIMEOUT) as client:
        response = await client.post(
            f"{_base_url()}/classify",
            headers=_headers(),
            json={
                "file_base64": file_base64,
                "modelRepo": model_repo,
                "modelFilename": model_filename,
            },
        )
        response.raise_for_status()
        return response.json()


async def detect(
    file_base64: str,
    model_repo: str = "Sendoc/sard-det",
    model_filename: str = "best.pt",
    confidence_threshold: float = 0.5,
) -> dict:
    """Appelle POST /detect et retourne la réponse JSON."""
    async with httpx.AsyncClient(timeout=settings.GPU_API_TIMEOUT) as client:
        response = await client.post(
            f"{_base_url()}/detect",
            headers=_headers(),
            json={
                "file_base64": file_base64,
                "config": {
                    "modelRepo": model_repo,
                    "modelFilename": model_filename,
                    "confidenceThreshold": confidence_threshold,
                },
            },
        )
        response.raise_for_status()
        return response.json()


async def extract_raw(
    system_prompt: str,
    user_prompt: str,
    model_id: str = "Qwen/Qwen3-8B",
    max_tokens: int = 512,
) -> dict:
    """Appelle POST /extract (LLM) et retourne la réponse JSON brute."""
    async with httpx.AsyncClient(timeout=settings.GPU_API_TIMEOUT) as client:
        response = await client.post(
            f"{_base_url()}/extract",
            headers=_headers(),
            json={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "modelId": model_id,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        return response.json()


# Prompt système pour l'extraction structurée
_EXTRACT_SYSTEM_PROMPT = (
    "/no_think "
    "Tu es un extracteur de données précis. "
    "Ton unique rôle est d'extraire une information spécifique depuis un texte fourni. "
    "## Règles de réponse "
    "- Réponds UNIQUEMENT en JSON valide, sans markdown, sans texte autour, sans explication. "
    "- Respecte scrupuleusement le schéma fourni : ne supprime, ne renomme et n'ajoute aucun attribut. "
    "- Remplace uniquement la valeur des attributs feuilles (sans enfants) par la donnée extraite. "
    "- Chaque attribut feuille doit contenir soit la valeur extraite du texte, "
    "soit null si l'information est absente. "
    "- Ne conserve jamais la description du schéma comme valeur : "
    "elle sert uniquement à guider l'extraction. "
    "- Une valeur vide ('') dans le schéma est également une description : remplace-la par null si l'information est absente. "
    "## Règles sur les objets imbriqués "
    "- Applique la même logique récursivement à chaque niveau du schéma. "
    "- Même si tous les attributs feuilles d'un objet sont null, l'objet et ses attributs doivent rester présents dans la réponse. "
    "- Aucun attribut ne doit jamais disparaître du schéma, quelle que soit la situation. "
)


async def extract_structured(
    text: str,
    schema: dict,
    field_descriptions: list[str] | None = None,
    model_id: str = "Qwen/Qwen3-8B",
    max_tokens: int = 1024,
) -> dict | None:
    """
    Extrait des données structurées depuis un texte selon un schéma JSON.
    Construit le prompt système + user, appelle /extract, parse la réponse JSON.
    Retourne None si l'appel ou le parsing échoue.
    """
    import json

    schema_str = json.dumps(schema, ensure_ascii=False)
    desc_block = ""
    if field_descriptions:
        desc_block = (
            "\nDescription des champs (chemin.vers.feuille: description):\n"
            + "\n".join(field_descriptions)
            + "\n"
        )
    user_prompt = f"Texte: {text}{desc_block}\nSchéma:{schema_str}"

    try:
        data = await extract_raw(
            _EXTRACT_SYSTEM_PROMPT, user_prompt, model_id, max_tokens,
        )
    except Exception:
        return None

    raw = data.get("response", "")
    clean = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    try:
        return json.loads(clean)
    except Exception:
        return None
