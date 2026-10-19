"""Modèles d'étiquettes prêts à l'emploi (menu « Modèles »)."""

from __future__ import annotations

from .models import Element, Label, LabelSettings


def blank(width_mm: float = 100, height_mm: float = 150) -> Label:
    return Label(LabelSettings(width_mm=width_mm, height_mm=height_mm), [])


def shipping_100x150() -> Label:
    """Bordereau d'expédition e-commerce."""
    s = LabelSettings(width_mm=100, height_mm=150)
    return Label(s, [
        Element("rect", "Cadre", x=3, y=3, width=94, height=144, thickness=0.5),
        Element("text", "Expéditeur", x=8, y=8, text="EXPÉDITEUR\nMa Boutique\n12 rue des Lilas\n62000 ARRAS", font_size=3.6),
        Element("rect", "Séparateur", x=3, y=36, width=94, height=0.5, filled=True),
        Element("text", "Destinataire", x=8, y=42, text="DESTINATAIRE", font_size=3.2, bold=True),
        Element("text", "Adresse", x=8, y=50, text="Jean DUPONT\n5 avenue de la République\n59000 LILLE", font_size=5.0, bold=True),
        Element("rect", "Séparateur 2", x=3, y=88, width=94, height=0.5, filled=True),
        Element("barcode", "Suivi", x=12, y=96, text="CMD20260001", bar_height=18, module=3),
        Element("qrcode", "QR", x=64, y=104, text="https://exemple.fr/suivi/CMD20260001", qr_cell=4),
        Element("text", "Commande", x=8, y=128, text="Commande n° 20260001\nPoids : 1,2 kg", font_size=3.6),
    ])


def product_50x30() -> Label:
    """Étiquette produit / prix avec code-barres EAN-13."""
    s = LabelSettings(width_mm=50, height_mm=30, gap_mm=2)
    return Label(s, [
        Element("text", "Titre", x=2, y=2, text="Console retro", font_size=4.2, bold=True),
        Element("text", "Prix", x=2, y=8, text="49,90 €", font_size=5.5, bold=True),
        Element("barcode", "EAN", x=2, y=16, symbology="ean13", text="590123412345", bar_height=9, module=2),
    ])


def address_70x40() -> Label:
    """Petite étiquette d'adresse."""
    s = LabelSettings(width_mm=70, height_mm=40, gap_mm=3)
    return Label(s, [
        Element("text", "Adresse", x=4, y=4, text="Jean DUPONT\n5 avenue de la République\n59000 LILLE", font_size=4.5),
        Element("qrcode", "QR", x=50, y=20, text="https://exemple.fr", qr_cell=4),
    ])


TEMPLATES = {
    "Expédition e-commerce 100 × 150 mm": shipping_100x150,
    "Produit + EAN-13 50 × 30 mm": product_50x30,
    "Adresse 70 × 40 mm": address_70x40,
}
