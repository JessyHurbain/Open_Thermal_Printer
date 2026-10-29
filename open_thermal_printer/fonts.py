"""Résolution de polices TrueType pour le rendu en mode raster.

On demande à fontconfig (`fc-match`) le fichier correspondant à une famille
(ex. « Liberation Sans ») ; en cas d'échec on retombe sur DejaVu, puis sur la
police intégrée de Pillow.
"""

from __future__ import annotations

import functools
import subprocess
from typing import cast

from PIL import ImageFont

_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)

_MONO_FALLBACKS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)


@functools.lru_cache(maxsize=128)
def find_font_file(family: str, bold: bool = False, italic: bool = False) -> str | None:
    pattern = family or "sans"
    if bold:
        pattern += ":bold"
    if italic:
        pattern += ":italic"
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@functools.lru_cache(maxsize=256)
def load_font(family: str, bold: bool, italic: bool, size_px: int) -> ImageFont.FreeTypeFont:
    size_px = max(4, int(size_px))
    candidates = [find_font_file(family, bold, italic), *_FALLBACKS]
    for path in candidates:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    # Avec une taille, Pillow charge sa police TrueType intégrée : le type est le bon, seules
    # les annotations de Pillow annoncent encore la classe bitmap.
    return cast(ImageFont.FreeTypeFont, ImageFont.load_default(size=size_px))


@functools.lru_cache(maxsize=64)
def load_mono_font(size_px: int) -> ImageFont.FreeTypeFont:
    """Police à chasse fixe (aperçu des polices natives de l'imprimante, texte des codes-barres)."""
    size_px = max(4, int(size_px))
    candidates = [find_font_file("monospace"), *_MONO_FALLBACKS]
    for path in candidates:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    return cast(ImageFont.FreeTypeFont, ImageFont.load_default(size=size_px))
