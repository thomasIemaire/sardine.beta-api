"""
Service commentaires sur les fichiers.
Un commentaire est lie a un fichier et a un utilisateur.
Les commentaires sont en lecture seule (pas de modification, seulement ajout et suppression).
"""

from datetime import UTC, datetime

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenError, NotFoundError
from app.features.auth.models import User
from app.features.permissions.service import check_folder_access

# ─── Modele ──────────────────────────────────────────────────────

class FileComment(Document):
    """Commentaire sur un fichier."""

    file_id: Indexed(PydanticObjectId)
    user_id: Indexed(PydanticObjectId)
    content: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "file_comments"


# ─── Schemas ─────────────────────────────────────────────────────

class CommentCreate(BaseModel):
    content: str


class CommentRead(BaseModel):
    id: str
    file_id: str
    user_id: str
    first_name: str
    last_name: str
    content: str
    created_at: datetime

    @classmethod
    def from_comment(cls, comment, user) -> "CommentRead":
        return cls(
            id=str(comment.id),
            file_id=str(comment.file_id),
            user_id=str(comment.user_id),
            first_name=user.first_name if user else "?",
            last_name=user.last_name if user else "?",
            content=comment.content,
            created_at=comment.created_at,
        )


# ─── Service ─────────────────────────────────────────────────────

async def _get_file_folder_id(file_id: str) -> str:
    """Recupere le folder_id d'un fichier pour verifier les droits."""
    from app.features.files.models import File
    f = await File.get(PydanticObjectId(file_id))
    if not f:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Fichier non trouve")
    return str(f.folder_id)


async def add_comment(
    user: User, file_id: str, content: str,
) -> FileComment:
    """Ajouter un commentaire (lecture sur le dossier suffit)."""
    folder_id = await _get_file_folder_id(file_id)
    await check_folder_access(str(user.id), folder_id)

    comment = FileComment(
        file_id=PydanticObjectId(file_id),
        user_id=user.id,
        content=content,
    )
    await comment.insert()
    return comment


async def list_comments(
    user: User, file_id: str,
    page: int = 1, page_size: int = 20,
) -> dict:
    """Liste les commentaires d'un fichier (pagine)."""
    from app.core.pagination import paginate

    folder_id = await _get_file_folder_id(file_id)
    await check_folder_access(str(user.id), folder_id)

    query = FileComment.find(
        FileComment.file_id == PydanticObjectId(file_id),
    )
    result = await paginate(query, page, page_size, sort_field="-created_at")

    # Enrichir avec les infos utilisateur
    enriched = []
    for c in result.items:
        u = await User.get(c.user_id)
        enriched.append(CommentRead.from_comment(c, u))

    return {
        "items": enriched,
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "total_pages": result.total_pages,
    }


async def delete_comment(
    user: User, comment_id: str,
) -> None:
    """Supprimer un commentaire (seul l'auteur peut supprimer)."""
    comment = await FileComment.get(PydanticObjectId(comment_id))
    if not comment:
        raise NotFoundError("Commentaire non trouve")
    if str(comment.user_id) != str(user.id):
        raise ForbiddenError("Vous ne pouvez supprimer que vos propres commentaires")
    await comment.delete()
