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
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import groupby

from PIL import Image

PDF_EXTS = (".pdf",)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
SUPPORTED_EXTS = PDF_EXTS + IMAGE_EXTS

Rect = tuple[int, int, int, int]

PREVIEW_DPI = 300  # résolution de la page affichée ; les rectangles de rognage sont exprimés dans ce repère


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
                import pymupdf  # PyMuPDF >= 1.24
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
        assert self._doc is not None  # sans image, le document PDF est ouvert
        pix = self._doc[max(0, min(page, self.page_count - 1))].get_pixmap(dpi=dpi, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    @property
    def is_pdf(self) -> bool:
        return self._doc is not None

    def page_text(self, page: int = 0) -> str:
        """Texte du PDF (vide pour une image : pas d'OCR)."""
        if not self._doc:
            return ""
        try:
            return self._doc[max(0, min(page, self.page_count - 1))].get_text()
        except Exception:
            return ""

    def text_blocks(self, page: int = 0) -> list[tuple[str, Rect]]:
        """Blocs de texte du PDF : (texte, rectangle) en repère PREVIEW_DPI ; vide pour une image."""
        if not self._doc:
            return []
        k = PREVIEW_DPI / 72  # points PDF -> pixels de la page affichée
        try:
            raw = self._doc[max(0, min(page, self.page_count - 1))].get_text("blocks")
        except Exception:
            return []
        found = []
        for block in raw:
            x0, y0, x1, y1, text = block[:5]
            if len(block) > 6 and block[6] != 0:  # 1 = image
                continue
            if text.strip():
                found.append((text, (int(x0 * k), int(y0 * k), int(x1 * k), int(y1 * k))))
        return found

    def text_rotation(self, page: int = 0, rect: Rect | None = None) -> int:
        """Rotation horaire (0, 90, 180, 270) qui redresse le contenu du PDF.

        Le rognage ne voit qu'une image : un bordereau dessiné à 90° ou à l'envers (fréquent chez
        certains transporteurs, Mondial Relay par exemple) se rognait correctement mais s'imprimait
        couché ou à 180°. La direction d'écriture des lignes de texte donne l'orientation réelle,
        sans OCR ni reconnaissance de code-barres.

        `rect` restreint la mesure à une zone (repère PREVIEW_DPI) : sur une page A4, les
        instructions sont droites et masqueraient l'orientation de l'étiquette elle-même.
        """
        if not self._doc:
            return 0
        try:
            raw = self._doc[max(0, min(page, self.page_count - 1))].get_text("dict")
        except Exception:
            return 0
        k = PREVIEW_DPI / 72
        weight: Counter[int] = Counter()  # quarts de tour horaires du contenu, pondérés par le texte
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                chars = len("".join(span.get("text", "") for span in line.get("spans", [])).strip())
                if not chars:
                    continue
                if rect is not None:
                    x0, y0, x1, y1 = (line.get("bbox") or block.get("bbox") or (0, 0, 0, 0))
                    cx, cy = (x0 + x1) / 2 * k, (y0 + y1) / 2 * k
                    if not (rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]):
                        continue  # texte hors de la zone rognée (mode d'emploi de la page)
                dx, dy = line.get("dir", (1, 0))
                quarter = int(round(math.degrees(math.atan2(dy, dx)) / 90)) % 4
                weight[quarter] += chars
        if not weight:
            return 0
        content = weight.most_common(1)[0][0]  # orientation du contenu : 0 = déjà droit
        return (4 - content) % 4 * 90  # rotation qui le redresse

    def render_region(self, page: int, rect: Rect, dpi: float) -> Image.Image:
        """Région `rect` (repère PREVIEW_DPI) rendue *directement* à `dpi` : net, sans double rééchantillonnage.

        Pour une image, on ne peut pas faire mieux que la découper.
        """
        if self._image is not None:
            return self._image.crop(rect)
        import pymupdf  # déjà chargé si le PDF a été ouvert

        k = 72 / PREVIEW_DPI
        clip = pymupdf.Rect(rect[0] * k, rect[1] * k, rect[2] * k, rect[3] * k)
        zoom = dpi / 72  # une matrice accepte un facteur décimal (le paramètre `dpi` exige un entier)
        assert self._doc is not None
        pix = self._doc[max(0, min(page, self.page_count - 1))].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
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
    return (box[0], box[1], box[2], box[3]) if box else None


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


def _area(rect: Rect) -> int:
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def _sub_blocks(mask: Image.Image, rect: Rect, gap: int, min_size: int) -> list[Rect]:
    """Blocs contenus dans `rect` (mêmes règles que `find_blocks`, coordonnées du masque)."""
    region = mask.crop(rect)
    found: list[Rect] = []
    for y0, y1 in _runs(region.getprojection()[1], gap):
        band = region.crop((0, y0, region.width, y1))
        for x0, x1 in _runs(band.getprojection()[0], gap):
            if x1 - x0 >= min_size and y1 - y0 >= min_size:
                found.append((rect[0] + x0, rect[1] + y0, rect[0] + x1, rect[1] + y1))
    return found


def _split_panels(mask: Image.Image, rect: Rect, gap: int, min_size: int) -> list[Rect]:
    """Découpe un bloc en panneaux de taille comparable (bordereau + notice côte à côte).

    Les bordereaux Colissimo occupent deux panneaux encadrés : la légende verticale imprimée
    dans la gouttière (« Étiquette Colissimo, découpez selon les pointillés ») rétrécit le blanc
    qui les sépare, si bien que la première passe les soude en un seul bloc. On retente avec un
    blanc plus fin, mais on ne garde le découpage que si les morceaux sont de taille comparable :
    une étiquette sans cadre, découpée en miettes (texte, code-barres…), reste d'un seul tenant.
    """
    parts = _sub_blocks(mask, rect, max(2, gap // 2), min_size)
    parts.sort(key=_area, reverse=True)
    if len(parts) < 2 or _area(parts[1]) < 0.6 * _area(parts[0]):
        return [rect]
    return parts


def find_blocks(img: Image.Image, gap_frac: float = 0.012, min_frac: float = 0.06) -> list[Rect]:
    """Blocs de contenu séparés par du blanc : bandes horizontales puis colonnes dans chaque bande.

    `gap_frac` : blanc minimal (en fraction de la plus grande dimension) pour séparer deux blocs.
    `min_frac` : taille minimale d'un bloc (fraction de la plus petite dimension), écarte les miettes.
    Résultat trié par surface décroissante (puis de haut en bas, de gauche à droite).
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
    blocks = [part for block in blocks for part in _split_panels(mask, block, gap, min_size)]

    inv = 1 / scale
    result = [
        (int(x0 * inv), int(y0 * inv), min(img.width, int(x1 * inv) + 1), min(img.height, int(y1 * inv) + 1))
        for x0, y0, x1, y1 in blocks
    ]
    result.sort(key=lambda r: (-_area(r), r[1], r[0]))
    return result


def auto_crop(img: Image.Image, texts: list[tuple[str, Rect]] | None = None) -> Rect:
    """Meilleure hypothèse pour « l'étiquette » : le plus grand bloc, sinon tout le contenu, sinon la page.

    `texts` : blocs de texte du document (`PageSource.text_blocks`). Ils servent à écarter la notice
    quand la feuille porte deux panneaux de taille comparable (bordereau + preuve de dépôt).
    """
    blocks = find_blocks(img)
    if blocks:
        return tighten(img, _label_block(blocks, texts))
    return content_bbox(img) or (0, 0, img.width, img.height)


def _label_block(blocks: list[Rect], texts: list[tuple[str, Rect]] | None) -> Rect:
    """Bloc du bordereau, quand plusieurs blocs de taille comparable coexistent sur la feuille."""
    rivals = [b for b in blocks if _area(b) >= 0.9 * _area(blocks[0])]
    if len(rivals) < 2 or not texts:
        return blocks[0]
    labels = [b for b in rivals if not _contains_notice(b, texts)]
    return labels[0] if len(labels) == 1 else blocks[0]


def _contains_notice(rect: Rect, texts: list[tuple[str, Rect]]) -> bool:
    inside = [text for text, r in texts if _center_in(r, rect)]
    return any(is_notice_text(text) for text in inside)


def _center_in(inner: Rect, outer: Rect) -> bool:
    cx, cy = (inner[0] + inner[2]) // 2, (inner[1] + inner[3]) // 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


# ---------------------------------------------------------------- mise à l'échelle
@dataclass
class Placement:
    image: Image.Image  # rogné, tourné, à la résolution finale quand c'est possible
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotated: bool  # tourné automatiquement de 90°
    scale: float = 0.0  # mm par pixel de la page (repère PREVIEW_DPI)
    turns: int = 0  # quarts de tour horaires appliqués au total
    module_dots: int | None = None  # largeur de barre calée en points (None : pas de calage)


# ---------------------------------------------------------------- calage des codes-barres
def estimate_module_px(img: Image.Image, max_run: int = 80, min_runs: int = 30) -> float | None:
    """Largeur de la barre étroite dominante (px), mesurée sur des lignes ET des colonnes échantillonnées.

    Un code-barres produit des centaines de segments noirs de largeur 1×, 2×, 3×… le module :
    la largeur la plus fréquente est donc le module. Renvoie None si le signal est trop faible.
    """
    mask = img.convert("L").point(lambda v: 255 if v < 128 else 0)
    best: tuple[int, int] | None = None  # (nombre de segments au pic, largeur)
    for m in (mask, mask.transpose(Image.Transpose.TRANSPOSE)):
        w, h = m.size
        data = m.tobytes()
        step = max(1, h // 200)
        counts: Counter[int] = Counter()
        for y in range(0, h, step):
            for value, group in groupby(data[y * w : (y + 1) * w]):
                if value:
                    n = sum(1 for _ in group)
                    if 2 <= n <= max_run:
                        counts[n] += 1
        if sum(counts.values()) < min_runs:
            continue
        smooth = {n: counts[n - 1] + counts[n] + counts[n + 1] for n in range(2, max_run + 1)}
        width = max(smooth, key=lambda n: smooth[n])
        if best is None or smooth[width] > best[0]:
            best = (smooth[width], width)
    return float(best[1]) if best else None


def snap_scale(scale: float, module_px: float, dpi: int, max_w_mm: float, max_h_mm: float,
               px_w: int, px_h: int) -> tuple[float, int | None]:
    """Ajuste l'échelle (mm/px) pour que la barre étroite tombe sur un nombre entier de points.

    Essaie d'abord l'entier supérieur (agrandissement ≤ 3 %, dans les marges), sinon l'inférieur
    (réduction ≤ 22 %). Sinon laisse l'échelle telle quelle. Renvoie (échelle, module en points).
    """
    dots_per_px = scale * dpi / 25.4
    ideal = module_px * dots_per_px
    if ideal < 1:
        return scale, None
    for target in (int(ideal) + 1, int(ideal)):
        if target < 1:
            continue
        ratio = target / ideal
        new = scale * ratio
        fits = px_w * new <= max_w_mm and px_h * new <= max_h_mm
        if (target > ideal and ratio <= 1.03 and fits) or (target <= ideal and ratio >= 0.78):
            return new, target
    return scale, None


def fit_to_label(crop: Image.Image, label_w_mm: float, label_h_mm: float, margin_mm: float = 2.0,
                 auto_rotate: bool = True, extra_rotation: int = 0, align_top: bool = False,
                 module_px: float | None = None, dpi: int = 203) -> Placement:
    """Ajuste `crop` dans l'étiquette (sans déformation), centré, éventuellement tourné de 90°.

    `extra_rotation` : rotation manuelle horaire (multiple de 90°) appliquée d'abord.
    `auto_rotate`   : tourne de 90° si cela permet un agrandissement plus grand.
    `module_px`     : largeur de barre étroite (px) → cale l'échelle sur un nombre entier de points.
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
            turns += 1

    module_dots = None
    if module_px:
        # la mesure du module est indépendante de l'orientation (lignes et colonnes testées)
        scale, module_dots = snap_scale(scale, module_px, dpi, label_w_mm - 1, label_h_mm - 1, img.width, img.height)

    w_mm, h_mm = img.width * scale, img.height * scale
    x = (label_w_mm - w_mm) / 2
    y = margin_mm if align_top else (label_h_mm - h_mm) / 2
    return Placement(img, round(x, 2), round(y, 2), round(w_mm, 2), round(h_mm, 2), rotated, scale, turns % 4,
                     module_dots)


def build_placement(source: PageSource | None, page: int, page_img: Image.Image, rect: Rect, label_w_mm: float,
                    label_h_mm: float, margin_mm: float, auto_rotate: bool, extra_rotation: int, dpi: int,
                    optimize_barcodes: bool, module_cache: dict | None = None, cache_key=None) -> Placement:
    """Placement final. Pour un PDF, la région est rendue directement à la résolution de l'imprimante.

    Un PDF dont le contenu est dessiné à 90°, 180° ou 270° est redressé d'abord : la rotation
    demandée par l'utilisateur (↻ 90°) s'ajoute à cette correction.
    """
    if source is not None:
        extra_rotation += source.text_rotation(page, rect)
    crop = page_img.crop(rect)
    module_px = None
    if optimize_barcodes:
        key = (cache_key, rect)
        if module_cache is not None and cache_key is not None and key in module_cache:
            module_px = module_cache[key]
        else:
            module_px = estimate_module_px(crop)
            if module_cache is not None and cache_key is not None:
                module_cache[key] = module_px
    place = fit_to_label(crop, label_w_mm, label_h_mm, margin_mm, auto_rotate, extra_rotation,
                         module_px=module_px, dpi=dpi)
    if source is None or not source.is_pdf:
        return place

    dots_per_px = place.scale * dpi / 25.4
    region = source.render_region(page, rect, PREVIEW_DPI * dots_per_px)
    for _ in range(place.turns):
        region = region.transpose(Image.Transpose.ROTATE_270)
    w_mm = region.width * 25.4 / dpi  # largeur exacte en points entiers : aucun rééchantillonnage ensuite
    h_mm = region.height * 25.4 / dpi
    x = (label_w_mm - w_mm) / 2
    y = (label_h_mm - h_mm) / 2
    return Placement(region, round(x, 3), round(y, 3), w_mm, h_mm, place.rotated, place.scale, place.turns,
                     place.module_dots)


# ---------------------------------------------------------------- reconnaissance du transporteur
# Mots-clés recherchés dans le texte du PDF (ordre = ordre d'affichage). Comparaison sans accents ni casse.
KNOWN_KEYWORDS = (
    "mondial relay", "colissimo", "chronopost", "relais colis", "vinted go", "dpd", "gls", "ups", "dhl", "fedex",
    "inpost", "boxtal", "la poste", "vinted", "leboncoin",
)
_PRETTY = {"ups": "UPS", "dpd": "DPD", "gls": "GLS", "dhl": "DHL"}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower())


# Mentions qui trahissent une notice ou une preuve de dépôt : ce panneau n'est pas le bordereau à coller.
NOTICE_MARKERS = (
    "comment utiliser", "preuve de depot", "conditions generales", "mode d emploi", "notice d utilisation",
    "a conserver",
)


def is_notice_text(text: str) -> bool:
    """Vrai si le texte appartient à une notice d'utilisation plutôt qu'au bordereau."""
    norm = _normalize(text).replace("'", " ").replace("’", " ")
    return any(marker in norm for marker in NOTICE_MARKERS)


def detect_keywords(text: str) -> list[str]:
    """Transporteurs / plateformes repérés dans le texte d'un bordereau."""
    norm = _normalize(text)
    return [k for k in KNOWN_KEYWORDS if re.search(rf"\b{re.escape(k)}\b", norm)]


def keywords_label(keywords: list[str]) -> str:
    return " + ".join(_PRETTY.get(k, k.title()) for k in keywords)


def match_preset(presets: dict, keywords: list[str], page_size: tuple[int, int], tolerance: float = 0.03) -> str | None:
    """Préréglage correspondant au document : tous ses mots-clés présents et même format de page.

    Le préréglage le plus spécifique (le plus de mots-clés) l'emporte. Un préréglage sans mots-clés
    n'est jamais appliqué automatiquement.
    """
    ratio = page_size[0] / page_size[1]
    best: tuple[int, str] | None = None
    for name, preset in presets.items():
        wanted = preset.get("match") or []
        if not wanted or not set(wanted) <= set(keywords):
            continue
        saved_ratio = preset.get("ratio")
        if saved_ratio and abs(saved_ratio - ratio) / saved_ratio > tolerance:
            continue
        if best is None or len(wanted) > best[0]:
            best = (len(wanted), name)
    return best[1] if best else None


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
