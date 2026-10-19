"""Import d'étiquettes de transport (PDF / image) : rasterisation, détection, rognage, mise à l'échelle.

Les bordereaux Vinted (Mondial Relay, Colissimo, Chronopost…) et Leboncoin sont des PDF A4 où
l'étiquette n'occupe qu'une partie de la page, entourée d'instructions. Ce module :
  1. rasterise la page (PyMuPDF) ou charge l'image ;
  2. repère les « blocs » de contenu séparés par du blanc (l'étiquette en fait partie) ;
  3. rogne, tourne si besoin et ajuste le résultat au format de l'étiquette thermique.

Les rectangles sont des tuples (x0, y0, x1, y1) en pixels de l'image de la page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from PIL import Image

PDF_EXTS = (".pdf",)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
SUPPORTED_EXTS = PDF_EXTS + IMAGE_EXTS

Rect = tuple[int, int, int, int]


class ImportError_(Exception):
    """Erreur d'import avec message affichable."""


# ---------------------------------------------------------------- chargement
class PageSource:
    """Document source : PDF multi-pages ou image unique."""

    def __init__(self, path: str):
        self.path = path
        self._doc = None
        self._image: Image.Image | None = None
        low = path.lower()
        if low.endswith(PDF_EXTS):
            try:
                import pymupdf  # PyMuPDF >= 1.24 ; l'ancien nom du module est « fitz »
            except ImportError:
                try:
                    import fitz as pymupdf
                except ImportError as exc:
                    raise ImportError_("PyMuPDF n'est pas installé :  pip install pymupdf") from exc
            try:
                self._doc = pymupdf.open(path)
            except Exception as exc:
                raise ImportError_(f"PDF illisible : {exc}") from exc
            if self._doc.needs_pass:
                raise ImportError_("Ce PDF est protégé par un mot de passe.")
            if self._doc.page_count == 0:
                raise ImportError_("Ce PDF ne contient aucune page.")
        elif low.endswith(IMAGE_EXTS):
            try:
                with Image.open(path) as im:
                    im.load()
                    self._image = _to_rgb(im)
            except Exception as exc:
                raise ImportError_(f"Image illisible : {exc}") from exc
        else:
            raise ImportError_("Format non pris en charge (PDF, PNG, JPG, BMP, WebP, TIFF).")

    @property
    def page_count(self) -> int:
        return self._doc.page_count if self._doc else 1

    def render(self, page: int = 0, dpi: int = 300) -> Image.Image:
        """Page rasterisée en RGB (les images sont renvoyées telles quelles)."""
        if self._image is not None:
            return self._image.copy()
        pix = self._doc[max(0, min(page, self.page_count - 1))].get_pixmap(dpi=dpi, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    def close(self) -> None:
        if self._doc:
            self._doc.close()
            self._doc = None


def _to_rgb(im: Image.Image) -> Image.Image:
    """Aplatit la transparence sur du blanc."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        return bg.convert("RGB")
    return im.convert("RGB")


# ---------------------------------------------------------------- détection de contenu
def _ink_mask(img: Image.Image, white_level: int = 235) -> Image.Image:
    """Masque 'L' : 255 là où il y a de l'encre (pixel plus sombre que `white_level`)."""
    return img.convert("L").point(lambda v: 255 if v < white_level else 0)


def _runs(profile: list[int], max_gap: int) -> list[tuple[int, int]]:
    """Suites de valeurs non nulles ; deux suites séparées de moins de `max_gap` sont fusionnées."""
    runs: list[list[int]] = []
    for i, v in enumerate(profile):
        if not v:
            continue
        if runs and i - runs[-1][1] <= max_gap:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    return [(a, b) for a, b in runs]


def content_bbox(img: Image.Image, white_level: int = 235) -> Rect | None:
    box = _ink_mask(img, white_level).getbbox()
    return tuple(box) if box else None


def tighten(img: Image.Image, rect: Rect, pad: int = 0) -> Rect:
    """Réduit `rect` à l'encre qu'il contient (supprime les marges blanches)."""
    x0, y0, x1, y1 = rect
    inner = content_bbox(img.crop(rect))
    if inner is None:
        return rect
    return (
        max(0, x0 + inner[0] - pad), max(0, y0 + inner[1] - pad),
        min(img.width, x0 + inner[2] + pad), min(img.height, y0 + inner[3] + pad),
    )


def find_blocks(img: Image.Image, gap_frac: float = 0.012, min_frac: float = 0.06) -> list[Rect]:
    """Blocs de contenu séparés par du blanc : bandes horizontales puis colonnes dans chaque bande.

    `gap_frac` : blanc minimal (en fraction de la plus grande dimension) pour séparer deux blocs.
    `min_frac` : taille minimale d'un bloc (fraction de la plus petite dimension), écarte les miettes.
    Résultat trié par surface décroissante.
    """
    # Travail sur une version réduite : la détection reste instantanée même à 300 dpi
    scale = min(1.0, 900 / max(img.size))
    small = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.BOX)
    mask = _ink_mask(small)
    gap = max(2, int(gap_frac * max(small.size)))
    min_size = max(4, int(min_frac * min(small.size)))

    blocks: list[Rect] = []
    for y0, y1 in _runs(mask.getprojection()[1], gap):
        band = mask.crop((0, y0, mask.width, y1))
        for x0, x1 in _runs(band.getprojection()[0], gap):
            if x1 - x0 >= min_size and y1 - y0 >= min_size:
                blocks.append((x0, y0, x1, y1))

    inv = 1 / scale
    result = [
        (int(x0 * inv), int(y0 * inv), min(img.width, int(x1 * inv) + 1), min(img.height, int(y1 * inv) + 1))
        for x0, y0, x1, y1 in blocks
    ]
    result.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    return result


def auto_crop(img: Image.Image) -> Rect:
    """Meilleure hypothèse pour « l'étiquette » : le plus grand bloc, sinon tout le contenu, sinon la page."""
    blocks = find_blocks(img)
    if blocks:
        return tighten(img, blocks[0])
    return content_bbox(img) or (0, 0, img.width, img.height)


# ---------------------------------------------------------------- mise à l'échelle
@dataclass
class Placement:
    image: Image.Image  # rogné (et éventuellement tourné), prêt à être posé
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotated: bool


def fit_to_label(crop: Image.Image, label_w_mm: float, label_h_mm: float, margin_mm: float = 2.0,
                 auto_rotate: bool = True, extra_rotation: int = 0, align_top: bool = False) -> Placement:
    """Ajuste `crop` dans l'étiquette (sans déformation), centré, éventuellement tourné de 90°.

    `extra_rotation` : rotation manuelle horaire (multiple de 90°) appliquée d'abord.
    `auto_rotate`   : tourne de 90° si cela permet un agrandissement plus grand.
    """
    img = crop
    turns = (extra_rotation // 90) % 4
    for _ in range(turns):
        img = img.transpose(Image.Transpose.ROTATE_270)

    avail_w = max(1.0, label_w_mm - 2 * margin_mm)
    avail_h = max(1.0, label_h_mm - 2 * margin_mm)
    scale = min(avail_w / img.width, avail_h / img.height)
    rotated = False
    if auto_rotate:
        turned = img.transpose(Image.Transpose.ROTATE_270)
        scale_turned = min(avail_w / turned.width, avail_h / turned.height)
        if scale_turned > scale * 1.02:  # au moins 2 % de mieux, sinon on ne tourne pas
            img, scale, rotated = turned, scale_turned, True

    w_mm, h_mm = img.width * scale, img.height * scale
    x = (label_w_mm - w_mm) / 2
    y = margin_mm if align_top else (label_h_mm - h_mm) / 2
    return Placement(img, round(x, 2), round(y, 2), round(w_mm, 2), round(h_mm, 2), rotated)


# ---------------------------------------------------------------- préréglages de rognage
# Les PDF d'un même transporteur ont toujours la même mise en page : on mémorise le rectangle
# en fractions de la page pour le réappliquer tel quel aux prochains envois.
def rect_to_fractions(rect: Rect, size: tuple[int, int]) -> list[float]:
    w, h = size
    return [round(rect[0] / w, 4), round(rect[1] / h, 4), round(rect[2] / w, 4), round(rect[3] / h, 4)]


def fractions_to_rect(fr: list[float], size: tuple[int, int]) -> Rect:
    w, h = size
    x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in fr)
    return int(x0 * w), int(y0 * h), max(int(x0 * w) + 1, int(x1 * w)), max(int(y0 * h) + 1, int(y1 * h))


def dump_presets(presets: dict) -> str:
    return json.dumps(presets, ensure_ascii=False)


def load_presets(text: str | None) -> dict:
    try:
        data = json.loads(text) if text else {}
        return {k: v for k, v in data.items() if isinstance(v, dict) and len(v.get("rect", [])) == 4}
    except (ValueError, AttributeError):
        return {}
