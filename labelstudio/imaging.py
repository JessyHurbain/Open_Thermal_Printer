"""Traitement d'image pour l'impression thermique : noir et blanc, tramage, encodage binaire.

Conventions : les images « 1 bit » de Pillow valent 255 pour le blanc et 0 pour le noir.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageChops, ImageOps

# Matrice de Bayer 4x4 (tramage ordonné), valeurs 0..15
_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)

DITHER_MODES = {
    "threshold": "Seuil (noir / blanc pur)",
    "floyd": "Floyd-Steinberg (photos)",
    "bayer": "Tramage ordonné (Bayer)",
}


# ---------------------------------------------------------------- encodage base64
def encode_image_file(path: str, max_side: int = 2000) -> str:
    """Charge une image quelconque et la renvoie en PNG base64 (taille bornée)."""
    with Image.open(path) as im:
        im.load()
        im = _flatten_alpha(im)
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def decode_image(b64: str) -> Image.Image | None:
    if not b64:
        return None
    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        im.load()
        return im
    except Exception:  # image corrompue : on ignore plutôt que de planter l'éditeur
        return None


def _flatten_alpha(im: Image.Image) -> Image.Image:
    """Les pixels transparents deviennent blancs (sinon ils sortiraient noirs)."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg.convert("RGB")
    return im.convert("RGB")


# ---------------------------------------------------------------- binarisation
def _bayer_threshold_map(width: int, height: int) -> Image.Image:
    tile = Image.new("L", (4, 4))
    tile.putdata([int((v + 0.5) * 255 / 16) for row in _BAYER4 for v in row])
    strip = Image.new("L", (width, 4))
    for x in range(0, width, 4):
        strip.paste(tile, (x, 0))
    full = Image.new("L", (width, height))
    for y in range(0, height, 4):
        full.paste(strip, (0, y))
    return full


def to_bilevel(gray: Image.Image, method: str = "floyd", threshold: int = 128) -> Image.Image:
    """Convertit une image en niveaux de gris vers du 1 bit (0 = noir)."""
    gray = gray.convert("L")
    if method == "floyd":
        return gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    if method == "bayer":
        diff = ImageChops.subtract(gray, _bayer_threshold_map(*gray.size))
        return diff.point(lambda v: 255 if v > 0 else 0).convert("1", dither=Image.Dither.NONE)
    return gray.point(lambda v: 255 if v >= threshold else 0).convert("1", dither=Image.Dither.NONE)


def prepare_image(b64: str, width_px: int, method: str, threshold: int, invert: bool) -> Image.Image | None:
    """Image encodée -> bitmap 1 bit de largeur `width_px` (ratio conservé)."""
    src = decode_image(b64)
    if src is None:
        return None
    gray = _flatten_alpha(src).convert("L")
    width_px = max(1, width_px)
    height_px = max(1, round(gray.height * width_px / gray.width))
    gray = gray.resize((width_px, height_px), Image.Resampling.LANCZOS)
    if invert:
        gray = ImageOps.invert(gray)
    return to_bilevel(gray, method, threshold)


# ---------------------------------------------------------------- rotation / compositing
def rotate_cw(img: Image.Image, angle: int) -> Image.Image:
    """Rotation horaire par pas de 90° (le sens de rotation des commandes TSPL/ZPL)."""
    angle %= 360
    if angle == 90:
        return img.transpose(Image.Transpose.ROTATE_270)
    if angle == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if angle == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def paste_dark(base: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """Superpose `img` sur `base` (mode L) en gardant le plus sombre — le noir l'emporte toujours."""
    img = img.convert("L")
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(base.width, x + img.width), min(base.height, y + img.height)
    if x1 <= x0 or y1 <= y0:
        return
    part = img.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    region = base.crop((x0, y0, x1, y1))
    base.paste(ImageChops.darker(region, part), (x0, y0))


# ---------------------------------------------------------------- encodage binaire
def pack_bitmap(img: Image.Image) -> tuple[int, int, bytes]:
    """Image 1 bit -> (octets par ligne, hauteur, données) au format BITMAP du TSPL.

    En TSPL (mode 0) un bit à 0 = point noir, 1 = blanc, MSB en premier : c'est exactement
    l'encodage natif des images « 1 » de Pillow. La largeur est complétée de blanc au
    multiple de 8.
    """
    img = img.convert("1", dither=Image.Dither.NONE)
    width_bytes = (img.width + 7) // 8
    if width_bytes * 8 != img.width:
        padded = Image.new("1", (width_bytes * 8, img.height), 1)
        padded.paste(img, (0, 0))
        img = padded
    return width_bytes, img.height, img.tobytes()


_INVERT_TABLE = bytes(255 - i for i in range(256))


def invert_bytes(data: bytes) -> bytes:
    """Inverse tous les bits (ZPL : 1 = noir, à l'opposé du TSPL)."""
    return data.translate(_INVERT_TABLE)


def content_bbox(gray: Image.Image) -> tuple[int, int, int, int] | None:
    """Rectangle englobant les pixels noirs d'une image (None si tout est blanc)."""
    return ImageOps.invert(gray.convert("L")).getbbox()
