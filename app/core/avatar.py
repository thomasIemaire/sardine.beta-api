"""
Générateur de photos de profil — gradient pastel aléatoire.

Crée une image carrée avec un dégradé multi-points
entre 2 à 3 couleurs pastel choisies aléatoirement.
Le résultat est unique pour chaque utilisateur.
"""

import math
import random
from pathlib import Path

from PIL import Image

# Dossier de stockage des avatars
AVATARS_DIR = Path("storage/avatars")

# Palette de couleurs pastel (R, G, B)
PASTEL_COLORS = [
    (186, 230, 253),  # bleu ciel
    (196, 181, 253),  # violet
    (252, 211, 77),   # jaune
    (253, 186, 186),  # rose
    (167, 243, 208),  # vert menthe
    (253, 230, 138),  # jaune doré
    (216, 180, 254),  # mauve
    (254, 202, 202),  # saumon
    (186, 253, 235),  # turquoise
    (253, 186, 253),  # fuchsia clair
    (220, 252, 231),  # vert pâle
    (254, 215, 170),  # pêche
]


def _interpolate(c1: tuple, c2: tuple, t: float) -> tuple:
    """Interpolation linéaire entre deux couleurs."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2, strict=True))


def generate_avatar(user_id: str, size: int = 256) -> str:
    """
    Génère un avatar gradient pastel unique et le sauvegarde.
    Retourne le chemin relatif du fichier.
    """
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)

    # Choisir 2-3 couleurs pastel aléatoires
    num_colors = random.randint(2, 3)
    colors = random.sample(PASTEL_COLORS, num_colors)

    # Positions aléatoires des centres de couleur
    # pour créer un gradient multi-directionnel
    centers = []
    for _ in colors:
        cx = random.uniform(0.1, 0.9)
        cy = random.uniform(0.1, 0.9)
        centers.append((cx, cy))

    img = Image.new("RGB", (size, size))
    pixels = img.load()

    for y in range(size):
        for x in range(size):
            nx = x / size
            ny = y / size

            # Calculer la distance pondérée à chaque centre
            total_weight = 0.0
            r, g, b = 0.0, 0.0, 0.0

            for i, (cx, cy) in enumerate(centers):
                dist = math.sqrt((nx - cx) ** 2 + (ny - cy) ** 2) + 0.01
                weight = 1.0 / (dist ** 2)
                total_weight += weight
                r += colors[i][0] * weight
                g += colors[i][1] * weight
                b += colors[i][2] * weight

            r = int(r / total_weight)
            g = int(g / total_weight)
            b = int(b / total_weight)

            pixels[x, y] = (
                min(255, r),
                min(255, g),
                min(255, b),
            )

    filename = f"{user_id}.webp"
    filepath = AVATARS_DIR / filename
    img.save(filepath, "WEBP", quality=85)

    return f"avatars/{filename}"
