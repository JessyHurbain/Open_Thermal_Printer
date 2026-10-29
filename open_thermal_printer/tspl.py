"""Génération de trames TSPL (TSC Printer Language).

Deux modes :
  * raster  — toute l'étiquette est rendue en bitmap puis envoyée par une seule commande
              BITMAP. C'est le mode WYSIWYG : accents, polices TrueType, rotations exactes.
  * native  — TEXT / BARCODE / QRCODE / BOX / BITMAP : très léger, mais limité aux polices
              internes de l'imprimante (pas de caractères hors CP1252).

Les lignes sont terminées par CRLF. `preview=True` remplace les données binaires par un
résumé lisible (pour l'affichage dans l'application).
"""

from __future__ import annotations

from .imaging import content_bbox, pack_bitmap
from .models import Label
from .renderer import Rendered, printer_anchor, render_label


def _quote(text: str) -> bytes:
    """Chaîne TSPL : guillemets échappés, encodage CP1252 (accents français)."""
    return b'"' + text.replace('"', '\\"').encode("cp1252", "replace") + b'"'


class _Writer:
    def __init__(self, preview: bool):
        self.buf = bytearray()
        self.preview = preview

    def line(self, text: str | bytes = "") -> None:
        self.buf += text.encode("cp1252", "replace") if isinstance(text, str) else text
        self.buf += b"\r\n"

    def bitmap(self, x: int, y: int, img) -> None:
        wb, h, data = pack_bitmap(img)
        head = f"BITMAP {x},{y},{wb},{h},0,".encode()
        self.buf += head
        self.buf += f"<{len(data)} octets>".encode() if self.preview else data
        self.buf += b"\r\n"


def build_tspl(label: Label, preview: bool = False) -> bytes:
    s = label.settings
    native = s.mode == "native"
    w = _Writer(preview)

    # --- En-tête : format du support et réglages d'impression
    w.line(f"SIZE {s.width_mm:g} mm,{s.height_mm:g} mm")
    if s.media == "gap":
        w.line(f"GAP {s.gap_mm:g} mm,0 mm")
    elif s.media == "blackmark":
        w.line(f"BLINE {s.gap_mm:g} mm,0 mm")
    else:
        w.line("GAP 0 mm,0 mm")
    w.line(f"DIRECTION {s.direction}")
    w.line("REFERENCE 0,0")
    w.line(f"DENSITY {s.density}")
    w.line(f"SPEED {s.speed}")
    if native:
        w.line("CODEPAGE 1252")
    w.line("CLS")

    image, rendered = render_label(label)

    if not native:
        bbox = content_bbox(image)
        if bbox:  # on n'envoie que la zone utile : beaucoup plus rapide en Bluetooth
            x0 = bbox[0] // 8 * 8
            w.bitmap(x0, bbox[1], image.crop((x0, bbox[1], bbox[2], bbox[3])))
    else:
        for r in rendered:
            if r.error:
                w.line(f"; ignoré : {r.el.display_name()} ({r.error})")
                continue
            _emit_native(w, r)

    w.line(f"PRINT 1,{max(1, s.copies)}")
    return bytes(w.buf)


def _emit_native(w: _Writer, r: Rendered) -> None:
    el, info, rot = r.el, r.native, r.el.rotation % 360

    if el.kind == "text":
        for text, dx, dy, lw, lh in info["lines"]:
            if not text:
                continue
            ax, ay = printer_anchor(r, dx, dy, lw, lh)
            w.line(
                b"TEXT %d,%d,\"%s\",%d,%d,%d," % (ax, ay, info["font"].encode(), rot, info["mult"], info["mult"])
                + _quote(text)
            )

    elif el.kind == "barcode":
        ax, ay = printer_anchor(r)
        wide = info["module"] * (3 if info["symbology"] == "code39" else 2)
        w.line(
            b"BARCODE %d,%d,\"%s\",%d,%d,%d,%d,%d," % (
                ax, ay, info["type"].encode(), info["height"], 1 if info["human"] else 0, rot, info["module"], wide
            )
            + _quote(info["data"])
        )

    elif el.kind == "qrcode":
        ax, ay = printer_anchor(r)
        w.line(b"QRCODE %d,%d,%s,%d,A,%d," % (ax, ay, info["ecc"].encode(), info["cell"], rot) + _quote(info["data"]))

    elif el.kind == "image":
        w.bitmap(r.x, r.y, r.img)

    elif el.kind == "rect":
        bw, bh = r.img.size
        if info["filled"]:
            w.line(f"BAR {r.x},{r.y},{bw},{bh}")
        else:
            w.line(f"BOX {r.x},{r.y},{r.x + bw},{r.y + bh},{info['thickness']}")
