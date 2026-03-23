"""
Validateurs réutilisables.
Politique de mot de passe :
  - Minimum 8 caractères
  - Au moins 1 majuscule
  - Au moins 1 chiffre
  - Au moins 1 caractère spécial
"""

import re

from app.core.exceptions import ValidationError

# Regex décomposée en lookaheads pour chaque contrainte de complexité
PASSWORD_PATTERN = re.compile(
    r"^(?=.*[A-Z])"        # au moins 1 majuscule
    r"(?=.*\d)"             # au moins 1 chiffre
    r"(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>?/`~])"  # au moins 1 caractère spécial
    r".{8,}$"               # minimum 8 caractères au total
)


def validate_password(password: str) -> str:
    """Valide la complexité du mot de passe. Lève ValidationError si non conforme."""
    if not PASSWORD_PATTERN.match(password):
        raise ValidationError(
            "Le mot de passe doit contenir au minimum 8 caractères, "
            "1 majuscule, 1 chiffre et 1 caractère spécial."
        )
    return password
