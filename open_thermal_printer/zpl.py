"""Génération de trames ZPL II (Zebra Programming Language).

Même principe que `tspl.py` : mode raster (une seule image ^GFA) ou mode natif
(^A0, ^BC/^B3/^BE/^B8/^BU, ^BQ, ^GB). En ZPL, 1 = noir (à l'inverse du TSPL).

Remarque : l'encodage UTF-8 (^CI28) est activé pour les accents en mode natif.
"""

from __future__ import annotations

from .imaging import content_bbox, invert_bytes, pack_bitmap
from .models import Label
from .renderer import Rendered, render_label, rotated_subrect

_ROT = {0: "N", 90: "R", 180: "I", 270: "B"}


def _gf(x: int, y: int, img, preview: bool) -> str:
    wb, h, data = pack_bitmap(img)
    total = wb * h
    hexdata = invert_bytes(data).hex().upper()
    if preview:
        hexdata = hexdata[:64] + f"…<{total} octets>"
    return f"^FO{x},{y}^GFA,{total},{total},{wb},{hexdata}^FS"


def _fd(text: str) -> str:
    """Données de champ : ^ et ~ sont réservés, on les remplace par leur code hexadécimal (^FH)."""
    return text.replace("_", "_5F").replace("^", "_5E").replace("~", "_7E")


def build_zpl(label: Label, preview: bool = False) -> bytes:
    s = label.settings
    native = s.mode == "native"
    out: list[str] = []

    out.append(f"~SD{min(30, s.density * 2)}")  # noirceur 0..30
    out.append("^XA")
    out.append("^CI28")
    out.append(f"^PW{s.width_dots}")
    out.append(f"^LL{s.height_dots}")
    out.append({"gap": "^MNY", "blackmark": "^MNM"}.get(s.media, "^MNN"))
    out.append(f"^PR{max(2, min(12, s.speed))}")
    out.append("^POI" if s.direction == 1 else "^PON")
    out.append("^LH0,0")

    image, rendered = render_label(label)
    if not native:
        bbox = content_bbox(image)
        if bbox:
            x0 = bbox[0] // 8 * 8
            out.append(_gf(x0, bbox[1], image.crop((x0, bbox[1], bbox[2], bbox[3])), preview))
    else:
        for r in rendered:
            if r.error:
                out.append(f"^FX ignoré : {r.el.display_name()} ({r.error})")
                continue
            out.extend(_emit_native(r, preview))

    out.append(f"^PQ{max(1, s.copies)}")
    out.append("^XZ")
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def _emit_native(r: Rendered, preview: bool) -> list[str]:
    el, info = r.el, r.native
    rot = _ROT.get(el.rotation % 360, "N")
    lines: list[str] = []

    if el.kind == "text":
        for text, dx, dy, lw, lh in info["lines"]:
            if not text:
                continue
            x, y, _, _ = rotated_subrect(r, dx, dy, lw, lh)
            width = max(1, lw // max(1, len(text)))  # largeur moyenne d'un caractère
            lines.append(f"^FO{x},{y}^A0{rot},{lh},{width}^FH_^FD{_fd(text)}^FS")

    elif el.kind == "barcode":
        h, hr = info["height"], "Y" if info["human"] else "N"
        cmd = {
            "code128": f"^BC{rot},{h},{hr},N,N",
            "code39": f"^B3{rot},N,{h},{hr},N",
            "ean13": f"^BE{rot},{h},{hr},N",
            "ean8": f"^B8{rot},{h},{hr},N",
            "upca": f"^BU{rot},{h},{hr},N,Y",
        }[info["symbology"]]
        lines.append(f"^BY{info['module']},3,{h}")
        lines.append(f"^FO{r.x},{r.y}{cmd}^FH_^FD{_fd(info['data'])}^FS")

    elif el.kind == "qrcode":
        if el.rotation % 360 == 0:
            lines.append(f"^FO{r.x},{r.y}^BQN,2,{info['cell']}^FH_^FD{info['ecc']}A,{_fd(info['data'])}^FS")
        else:  # ^BQ ne gère pas la rotation : on envoie le QR déjà tourné en bitmap
            lines.append(_gf(r.x, r.y, r.img, preview))

    elif el.kind == "image":
        lines.append(_gf(r.x, r.y, r.img, preview))

    elif el.kind == "rect":
        bw, bh = r.img.size
        t = min(bw, bh) if info["filled"] else info["thickness"]
        lines.append(f"^FO{r.x},{r.y}^GB{bw},{bh},{t}^FS")

    return lines
