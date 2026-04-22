import json

import httpx

from app.config import settings

HF_API_BASE = "https://huggingface.co"
HF_API_MODELS = "https://huggingface.co/api/models"
REPO_ID = "Sendoc/sard-cls"


async def _hf_headers() -> dict:
    headers = {}
    if settings.HF_TOKEN:
        headers["Authorization"] = f"Bearer {settings.HF_TOKEN}"
    return headers


async def list_classifier_versions() -> list[dict]:
    headers = await _hf_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        # Fetch all refs (tags) for the repo
        refs_resp = await client.get(
            f"{HF_API_BASE}/api/models/{REPO_ID}/refs",
            headers=headers,
        )
        refs_resp.raise_for_status()
        refs = refs_resp.json()

    tags: list[dict] = refs.get("tags", [])

    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for tag in tags:
            tag_name = tag["name"]

            # Read classes.json at this specific revision
            classes_resp = await client.get(
                f"{HF_API_BASE}/{REPO_ID}/resolve/{tag_name}/classes.json",
                headers=headers,
                follow_redirects=True,
            )

            classes: list[str] = []
            if classes_resp.status_code == 200:
                try:
                    data = classes_resp.json()
                    classes = data.get("classes", [])
                except (json.JSONDecodeError, AttributeError):
                    pass

            results.append({
                "model": REPO_ID,
                "version": tag_name,
                "classes": classes,
            })

    return results


async def get_latest_classifier_classes() -> list[str]:
    """Retourne les classes de la dernière version du modèle (tag le plus récent)."""
    headers = await _hf_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        refs_resp = await client.get(
            f"{HF_API_BASE}/api/models/{REPO_ID}/refs",
            headers=headers,
        )
        refs_resp.raise_for_status()
        tags: list[dict] = refs_resp.json().get("tags", [])

        if not tags:
            return []

        latest_tag = tags[-1]["name"]
        classes_resp = await client.get(
            f"{HF_API_BASE}/{REPO_ID}/resolve/{latest_tag}/classes.json",
            headers=headers,
            follow_redirects=True,
        )

    if classes_resp.status_code != 200:
        return []

    try:
        return classes_resp.json().get("classes", [])
    except (json.JSONDecodeError, AttributeError):
        return []
