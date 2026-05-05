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
    model_version: str | None = None,
) -> dict:
    """Appelle POST /classify et retourne la réponse JSON."""
    payload: dict = {
        "file_base64": file_base64,
        "modelRepo": model_repo,
        "modelFilename": model_filename,
    }
    if model_version:
        payload["modelVersion"] = model_version

    async with httpx.AsyncClient(timeout=settings.GPU_API_TIMEOUT) as client:
        response = await client.post(
            f"{_base_url()}/classify",
            headers=_headers(),
            json=payload,
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
        if response.status_code >= 400:
            body = response.text
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} sur /extract — body: {body}",
                request=response.request,
                response=response,
            )
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
    "## Règles sur les tableaux "
    "- Si la valeur d'un attribut est un tableau JSON, ce tableau contient un unique élément template décrivant la forme attendue de chaque item. "
    "- Tu dois retourner autant d'éléments que d'occurrences réellement trouvées dans le texte (zéro, un ou plusieurs). "
    "- Si aucune occurrence n'est trouvée, retourne un tableau vide []. "
    "- Chaque élément du tableau retourné doit respecter EXACTEMENT la forme du template : mêmes clés, même imbrication, sans attribut en plus, sans attribut en moins. "
    "- Les feuilles de chaque élément suivent les mêmes règles que les feuilles hors tableau : valeur extraite ou null si absente. "
    "- Les contraintes 'aucun attribut ne disparaît' et 'pas de renommage' s'appliquent aux clés de chaque élément ; le nombre d'éléments du tableau, lui, est libre. "
)


class ExtractError(RuntimeError):
    """Erreur d'extraction LLM avec contexte (HTTP, parsing, troncature…).

    Attributs supplémentaires accessibles pour la metadata du nœud :
      - raw_response : str | None — contenu brut renvoyé par le LLM
      - request_meta : dict       — taille du prompt, schéma, max_tokens, modèle
      - kind         : str        — 'http' | 'empty' | 'json' | 'truncated'
    """

    def __init__(
        self,
        message: str,
        kind: str = "unknown",
        raw_response: str | None = None,
        request_meta: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.raw_response = raw_response
        self.request_meta = request_meta or {}


async def extract_structured(
    text: str,
    schema: dict,
    field_descriptions: list[str] | None = None,
    model_id: str = "Qwen/Qwen3-8B",
    max_tokens: int = 1024,
) -> dict:
    """
    Extrait des données structurées depuis un texte selon un schéma JSON.
    Construit le prompt système + user, appelle /extract, parse la réponse JSON.
    Lève ExtractError (avec contexte) si l'appel HTTP échoue ou si le JSON est
    invalide.
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

    request_meta = {
        "model_id": model_id,
        "max_tokens": max_tokens,
        "user_prompt_chars": len(user_prompt),
        "schema_chars": len(schema_str),
        "ocr_text_chars": len(text),
        "field_descriptions_count": len(field_descriptions or []),
    }

    try:
        data = await extract_raw(
            _EXTRACT_SYSTEM_PROMPT, user_prompt, model_id, max_tokens,
        )
    except Exception as exc:
        raise ExtractError(
            f"appel HTTP /extract échoué: {exc}",
            kind="http",
            request_meta=request_meta,
        ) from exc

    raw = data.get("response", "")
    clean = (
        raw.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    if not clean:
        raise ExtractError(
            "réponse LLM vide",
            kind="empty",
            raw_response=raw,
            request_meta=request_meta,
        )

    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        truncated = not clean.rstrip().endswith(("}", "]"))
        truncated_hint = " (probablement tronqué — augmenter max_tokens)" if truncated else ""
        raise ExtractError(
            f"JSON invalide{truncated_hint}: {exc.msg} à pos {exc.pos}.\n"
            f"--- Body complet ({len(clean)} chars) ---\n{clean}\n--- fin body ---",
            kind="truncated" if truncated else "json",
            raw_response=clean,
            request_meta=request_meta,
        ) from exc
