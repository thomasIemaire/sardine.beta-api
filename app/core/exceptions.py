"""
Exceptions HTTP réutilisables.
Chaque exception encapsule un code HTTP et un message par défaut.
Les messages côté client restent génériques pour ne pas divulguer
d'informations sensibles.
"""

from fastapi import HTTPException, status


class NotFoundError(HTTPException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Invalid credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(HTTPException):
    def __init__(self, detail: str = "Not enough permissions"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class ConflictError(HTTPException):
    def __init__(self, detail: str = "Resource already exists"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class ValidationError(HTTPException):
    """Erreur de validation métier (ex: politique de mot de passe)."""
    def __init__(self, detail: str = "Validation failed"):
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


class LockedError(HTTPException):
    """Compte temporairement verrouillé (protection brute force)."""
    def __init__(self, detail: str = "Account temporarily locked. Try again later."):
        super().__init__(status_code=status.HTTP_423_LOCKED, detail=detail)
