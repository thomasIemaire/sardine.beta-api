from fastapi import APIRouter, HTTPException

from app.features.auth.dependencies import CurrentUser
from app.features.classifiers.service import list_classifier_versions

router = APIRouter(prefix="/classifiers", tags=["Classifiers"])


@router.get("/versions")
async def get_classifier_versions(current_user: CurrentUser) -> list[dict]:
    """Retourne la liste des versions (tags) du modèle de classification avec leurs classes."""
    try:
        return await list_classifier_versions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erreur HuggingFace : {exc}") from exc
