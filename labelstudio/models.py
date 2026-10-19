"""Modèle de données : une étiquette = des réglages + une liste d'éléments.

Toutes les positions et dimensions sont stockées en millimètres. La conversion
en points imprimante (« dots ») se fait au moment du rendu, selon la résolution
(203 dpi pour la PM-241-BT, soit ~8 points/mm).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

KINDS = ("text", "barcode", "qrcode", "image", "rect")

KIND_LABELS = {
    "text": "Texte",
    "barcode": "Code-barres",
    "qrcode": "QR code",
    "image": "Image",
    "rect": "Rectangle",
}

# Symbologies proposées : identifiant python-barcode -> libellé affiché
SYMBOLOGIES = {
    "code128": "Code 128",
    "code39": "Code 39",
    "ean13": "EAN-13",
    "ean8": "EAN-8",
    "upca": "UPC-A",
}


@dataclass
class Element:
    """Un objet posé sur l'étiquette. Les champs inutiles pour un type donné sont ignorés."""

    kind: str = "text"
    name: str = ""
    # Position (mm) du coin haut-gauche de l'élément *après rotation*
    x: float = 5.0
    y: float = 5.0
    rotation: int = 0  # 0, 90, 180, 270 (sens horaire)

    # Contenu : texte affiché, données du code-barres ou du QR code
    text: str = ""

    # --- Texte ---
    font_family: str = "DejaVu Sans"
    font_size: float = 5.0  # hauteur du corps de police, en mm
    bold: bool = False
    italic: bool = False
    align: str = "left"  # left | center | right

    # --- Code-barres ---
    symbology: str = "code128"
    bar_height: float = 12.0  # mm
    module: int = 2  # largeur d'une barre étroite, en points
    human_readable: bool = True

    # --- QR code ---
    qr_cell: int = 6  # taille d'un module, en points
    qr_ecc: str = "M"  # L | M | Q | H

    # --- Image (PNG encodé en base64 pour que le projet reste portable) ---
    image_b64: str = ""
    image_name: str = ""
    threshold: int = 128
    dither: str = "floyd"  # threshold | floyd | bayer
    invert: bool = False

    # --- Image / Rectangle ---
    width: float = 20.0  # mm
    height: float = 10.0  # mm (rectangle uniquement ; l'image garde son ratio)
    thickness: float = 0.4  # mm (rectangle)
    filled: bool = False

    def display_name(self) -> str:
        label = KIND_LABELS.get(self.kind, self.kind)
        if self.name:
            return f"{label} — {self.name}"
        if self.kind in ("text", "barcode", "qrcode") and self.text:
            snippet = self.text.replace("\n", " ⏎ ")
            return f"{label} — {snippet[:28]}"
        if self.kind == "image" and self.image_name:
            return f"{label} — {self.image_name}"
        return label

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Element":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class LabelSettings:
    """Paramètres du support et de l'impression."""

    width_mm: float = 100.0
    height_mm: float = 150.0
    gap_mm: float = 3.0
    media: str = "gap"  # gap | blackmark | continuous
    dpi: int = 203
    density: int = 8  # 0..15 (noirceur)
    speed: int = 4  # 1..10
    direction: int = 0  # 0 | 1 (commande DIRECTION du TSPL)
    language: str = "tspl"  # tspl | zpl
    mode: str = "raster"  # raster (WYSIWYG) | native (commandes TEXT/BARCODE/QRCODE)
    copies: int = 1

    @property
    def dots_per_mm(self) -> float:
        return self.dpi / 25.4

    def to_dots(self, mm: float) -> int:
        return int(round(mm * self.dots_per_mm))

    @property
    def width_dots(self) -> int:
        return max(1, self.to_dots(self.width_mm))

    @property
    def height_dots(self) -> int:
        return max(1, self.to_dots(self.height_mm))


@dataclass
class Label:
    settings: LabelSettings = field(default_factory=LabelSettings)
    elements: list[Element] = field(default_factory=list)

    # --- Sérialisation (fichiers projet .label.json) ---
    def to_json(self) -> str:
        payload = {
            "version": 1,
            "settings": asdict(self.settings),
            "elements": [e.to_dict() for e in self.elements],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Label":
        data = json.loads(text)
        known = {f.name for f in fields(LabelSettings)}
        settings = LabelSettings(**{k: v for k, v in data.get("settings", {}).items() if k in known})
        elements = [Element.from_dict(d) for d in data.get("elements", [])]
        return cls(settings=settings, elements=elements)
