"""Rendu des éléments en bitmaps (aperçu à l'écran *et* impression en mode raster).

Chaque élément est rendu vers une image « 1 bit » à la résolution de l'imprimante, puis
tourné par pas de 90°. L'objet `Rendered` conserve en plus les paramètres nécessaires aux
générateurs de commandes natives (TEXT / BARCODE / QRCODE en TSPL, ^A / ^B* en ZPL).

Convention de position : (x, y) est le coin haut-gauche de l'élément *après* rotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import qrcode
from PIL import Image, ImageDraw
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

from .fonts import load_font, load_mono_font
from .imaging import paste_dark, prepare_image, rotate_cw
from .models import Element, Label, LabelSettings

# Polices bitmap intégrées aux imprimantes TSPL à 203 dpi : id -> (largeur, hauteur) en points
NATIVE_FONTS = {"1": (8, 12), "2": (12, 20), "3": (16, 24), "4": (24, 32), "5": (32, 48)}

# Symbologie -> nom TSPL
TSPL_BARCODE_NAMES = {"code128": "128", "code39": "39", "ean13": "EAN13", "ean8": "EAN8", "upca": "UPCA"}

_QR_ECC = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}


@dataclass
class Rendered:
    el: Element
    img: Image.Image  # image finale (après rotation), mode "1" ou "L"
    x: int  # coin haut-gauche de `img` sur l'étiquette, en points
    y: int
    uw: int  # dimensions avant rotation
    uh: int
    native: dict = field(default_factory=dict)  # paramètres pour les commandes natives
    error: str | None = None  # si renseigné : élément non imprimable (placeholder dessiné)


# ------------------------------------------------------------------ géométrie
def rotated_subrect(r: Rendered, rx: int, ry: int, rw: int, rh: int) -> tuple[int, int, int, int]:
    """Position d'un sous-rectangle (défini avant rotation) sur l'étiquette, après rotation.

    Renvoie (x, y, w, h) du rectangle tourné, en points sur l'étiquette.
    """
    rot = r.el.rotation % 360
    if rot == 90:
        bx, by, bw, bh = r.uh - ry - rh, rx, rh, rw
    elif rot == 180:
        bx, by, bw, bh = r.uw - rx - rw, r.uh - ry - rh, rw, rh
    elif rot == 270:
        bx, by, bw, bh = ry, r.uw - rx - rw, rh, rw
    else:
        bx, by, bw, bh = rx, ry, rw, rh
    return r.x + bx, r.y + by, bw, bh


def printer_anchor(r: Rendered, rx: int = 0, ry: int = 0, rw: int | None = None, rh: int | None = None) -> tuple[int, int]:
    """Point d'ancrage à donner à TEXT / BARCODE / QRCODE (TSPL) pour obtenir le résultat de l'aperçu.

    En TSPL, la rotation se fait autour du point (x, y) *sans* ré-aligner le coin haut-gauche :
    pour 90° le texte s'étend vers la gauche et le bas du point, etc.
    """
    rw = r.uw if rw is None else rw
    rh = r.uh if rh is None else rh
    bx, by, _, _ = rotated_subrect(r, rx, ry, rw, rh)
    rot = r.el.rotation % 360
    if rot == 90:
        return bx + rh, by
    if rot == 180:
        return bx + rw, by + rh
    if rot == 270:
        return bx, by + rw
    return bx, by


# ------------------------------------------------------------------ placeholders
def _error_box(message: str, s: LabelSettings) -> Image.Image:
    w, h = s.to_dots(30), s.to_dots(10)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w - 1, h - 1), outline=0, width=2)
    d.line((0, 0, w - 1, h - 1), fill=0, width=1)
    d.line((0, h - 1, w - 1, 0), fill=0, width=1)
    font = load_mono_font(max(10, s.to_dots(2.2)))
    tw = font.getlength(message)
    d.rectangle((4, h // 2 - 12, min(w - 4, int(tw) + 12), h // 2 + 12), fill=255)
    d.text((8, h // 2), message, font=font, fill=0, anchor="lm")
    return img


# ------------------------------------------------------------------ texte
def _safe_cp1252(text: str) -> str:
    """Texte tel que l'imprimante le recevra (code page 1252, '?' pour le reste)."""
    return text.encode("cp1252", "replace").decode("cp1252")


def pick_native_font(height_dots: int) -> tuple[str, int]:
    """Choisit (police, multiplicateur) dont la hauteur approche le mieux la hauteur demandée."""
    best = None
    for font_id, (_fw, fh) in NATIVE_FONTS.items():
        mult = min(10, max(1, round(height_dots / fh)))
        score = (abs(fh * mult - height_dots), -fh)
        if best is None or score < best[0]:
            best = (score, font_id, mult)
    assert best is not None  # NATIVE_FONTS n'est jamais vide
    return best[1], best[2]


def _align_offset(width: int, avail: int, align: str) -> int:
    if align == "center":
        return (avail - width) // 2
    if align == "right":
        return avail - width
    return 0


def _render_text(el: Element, s: LabelSettings, native: bool) -> tuple[Image.Image, dict]:
    lines = (el.text or " ").split("\n")
    info: dict = {}
    if native:
        font_id, mult = pick_native_font(s.to_dots(el.font_size))
        fw, fh = NATIVE_FONTS[font_id]
        cw, ch = fw * mult, fh * mult
        pitch = ch + 2 * mult
        lines = [_safe_cp1252(line) for line in lines]
        total_w = max(1, max(len(line) for line in lines) * cw)
        total_h = pitch * len(lines) - 2 * mult
        img = Image.new("L", (total_w, total_h), 255)
        mono = load_mono_font(int(ch * 0.85))
        placed = []
        for i, line in enumerate(lines):
            lw = len(line) * cw
            dx, dy = _align_offset(lw, total_w, el.align), i * pitch
            placed.append((line, dx, dy, lw, ch))
            if not line:
                continue
            natural = Image.new("L", (max(1, int(mono.getlength(line)) + 2), int(ch * 1.4)), 255)
            ImageDraw.Draw(natural).text((0, 0), line, font=mono, fill=0)
            bbox = natural.point(lambda v: 255 - v).getbbox()
            if bbox:  # recadre l'encre puis étire à la grille de la police imprimante
                glyphs = natural.crop((0, 0, bbox[2], int(ch * 1.2))).resize((lw, ch), Image.Resampling.BOX)
                img.paste(glyphs.point(lambda v: 0 if v < 160 else 255), (dx, dy))
        info = {"font": font_id, "mult": mult, "lines": placed}
        return img, info

    font = load_font(el.font_family, el.bold, el.italic, s.to_dots(el.font_size))
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    widths = [int(font.getlength(line)) + 1 for line in lines]
    total_w, total_h = max(1, max(widths)), line_h * len(lines)
    img = Image.new("L", (total_w, total_h), 255)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # pas d'antialiasing : le thermique est binaire
    placed = []
    for i, line in enumerate(lines):
        dx, dy = _align_offset(widths[i], total_w, el.align), i * line_h
        draw.text((dx, dy), line, font=font, fill=0)
        placed.append((line, dx, dy, widths[i], line_h))
    return img, {"lines": placed}


# ------------------------------------------------------------------ code-barres
def barcode_modules(symbology: str, data: str) -> tuple[str, str]:
    """(suite de modules '1010…', texte lisible) ; lève une exception si les données sont invalides."""
    import barcode

    cls = barcode.get_barcode_class(symbology)
    kwargs = {"add_checksum": False} if symbology == "code39" else {}
    obj = cls(data, **kwargs)
    return "".join(obj.build()), obj.get_fullcode()


def _render_barcode(el: Element, s: LabelSettings) -> tuple[Image.Image, dict]:
    if not el.text.strip():
        raise ValueError("Données vides")
    bits, human = barcode_modules(el.symbology, el.text.strip())
    module = max(1, int(el.module))
    bar_h = max(2, s.to_dots(el.bar_height))
    bars_w = len(bits) * module

    text_h, text_w, font = 0, 0, None
    if el.human_readable:
        font = load_mono_font(max(12, s.to_dots(2.6)))
        asc, desc = font.getmetrics()
        text_h = asc + desc + 3
        text_w = int(font.getlength(human)) + 2
    total_w = max(bars_w, text_w)
    x0 = (total_w - bars_w) // 2
    img = Image.new("L", (total_w, bar_h + text_h), 255)
    draw = ImageDraw.Draw(img)
    i = 0
    while i < len(bits):  # un rectangle par suite de barres, plus rapide qu'un pixel par module
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            draw.rectangle((x0 + i * module, 0, x0 + j * module - 1, bar_h - 1), fill=0)
            i = j
        else:
            i += 1
    if font is not None:
        draw.fontmode = "1"
        draw.text((total_w // 2, bar_h + 2), human, font=font, fill=0, anchor="ma")
    info = {
        "type": TSPL_BARCODE_NAMES.get(el.symbology, "128"),
        "symbology": el.symbology,
        "height": bar_h,
        "module": module,
        "human": el.human_readable,
        "data": el.text.strip(),
    }
    return img, info


# ------------------------------------------------------------------ QR code
def _render_qr(el: Element, s: LabelSettings) -> tuple[Image.Image, dict]:
    qr = qrcode.QRCode(error_correction=_QR_ECC.get(el.qr_ecc, ERROR_CORRECT_M), box_size=1, border=0)
    qr.add_data(el.text if el.text else " ")
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    cell = max(1, int(el.qr_cell))
    small = Image.new("L", (n, n), 255)
    small.putdata([0 if c else 255 for row in matrix for c in row])
    img = small.resize((n * cell, n * cell), Image.Resampling.NEAREST)
    return img, {"ecc": el.qr_ecc, "cell": cell, "data": el.text, "modules": n}


# ------------------------------------------------------------------ image / rectangle
def _render_image(el: Element, s: LabelSettings) -> Image.Image:
    img = prepare_image(el.image_b64, s.to_dots(el.width), el.dither, el.threshold, el.invert)
    if img is None:
        raise ValueError("Image manquante")
    return img


def _render_rect(el: Element, s: LabelSettings) -> tuple[Image.Image, dict]:
    w, h = max(1, s.to_dots(el.width)), max(1, s.to_dots(el.height))
    t = max(1, s.to_dots(el.thickness))
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    if el.filled:
        d.rectangle((0, 0, w - 1, h - 1), fill=0)
    else:
        d.rectangle((0, 0, w - 1, h - 1), outline=0, width=min(t, (min(w, h) + 1) // 2))
    return img, {"thickness": t, "filled": el.filled}


# ------------------------------------------------------------------ API publique
def render_element(el: Element, s: LabelSettings, native: bool = False) -> Rendered:
    error = None
    info: dict = {}
    try:
        if el.kind == "text":
            img, info = _render_text(el, s, native)
        elif el.kind == "barcode":
            img, info = _render_barcode(el, s)
        elif el.kind == "qrcode":
            img, info = _render_qr(el, s)
        elif el.kind == "image":
            img = _render_image(el, s)
        elif el.kind == "rect":
            img, info = _render_rect(el, s)
        else:
            raise ValueError(f"Type inconnu : {el.kind}")
    except Exception as exc:  # donnée invalide : on dessine un placeholder au lieu de planter
        error = str(exc) or exc.__class__.__name__
        img = _error_box(error[:24], s)
    uw, uh = img.size
    img = rotate_cw(img, el.rotation)
    return Rendered(el, img, s.to_dots(el.x), s.to_dots(el.y), uw, uh, info, error)


def render_label(label: Label) -> tuple[Image.Image, list[Rendered]]:
    """Rend toute l'étiquette. Renvoie (image « L » pleine taille, éléments rendus dans l'ordre)."""
    s = label.settings
    native = s.mode == "native"
    base = Image.new("L", (s.width_dots, s.height_dots), 255)
    rendered = []
    for el in label.elements:
        r = render_element(el, s, native)
        paste_dark(base, r.img, r.x, r.y)
        rendered.append(r)
    return base, rendered


def warnings_for(rendered: list[Rendered]) -> list[str]:
    return [f"{r.el.display_name()} : {r.error}" for r in rendered if r.error]
