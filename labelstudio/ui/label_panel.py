"""Panneau « Étiquette » : dimensions, support, résolution, langage et mode d'impression."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QWidget

from ..models import LabelSettings

# Formats courants : libellé -> (largeur mm, hauteur mm)
PRESETS = {
    "100 × 150 mm (expédition)": (100, 150),
    "100 × 100 mm": (100, 100),
    "100 × 50 mm": (100, 50),
    "70 × 40 mm": (70, 40),
    "60 × 40 mm": (60, 40),
    "50 × 30 mm": (50, 30),
    "40 × 30 mm": (40, 30),
    "30 × 20 mm": (30, 20),
}


class LabelPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        self.settings = LabelSettings()
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.preset = QComboBox()
        self.preset.addItem("Personnalisé", None)
        for name, size in PRESETS.items():
            self.preset.addItem(name, size)
        self.unit = QComboBox()
        self.unit.addItem("millimètres (mm)", "mm")
        self.unit.addItem("points imprimante (dots)", "dots")
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for w in (self.width, self.height):
            w.setDecimals(1)
        self.media = QComboBox()
        for key, txt in (("gap", "Étiquettes avec espacement (gap)"), ("blackmark", "Repère noir au dos"),
                         ("continuous", "Papier continu")):
            self.media.addItem(txt, key)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0, 20)
        self.gap.setDecimals(1)
        self.gap.setSuffix(" mm")
        self.dpi = QComboBox()
        for d in (203, 300):
            self.dpi.addItem(f"{d} dpi", d)
        self.density = QSpinBox()
        self.density.setRange(0, 15)
        self.density.setToolTip("Noirceur de l'impression (0 = clair, 15 = très foncé)")
        self.speed = QSpinBox()
        self.speed.setRange(1, 10)
        self.direction = QComboBox()
        self.direction.addItem("0 (normal)", 0)
        self.direction.addItem("1 (tête-bêche)", 1)
        self.language = QComboBox()
        self.language.addItem("TSPL (recommandé)", "tspl")
        self.language.addItem("ZPL II", "zpl")
        self.mode = QComboBox()
        self.mode.addItem("Raster — rendu identique à l'aperçu", "raster")
        self.mode.addItem("Natif — commandes TEXT / BARCODE / QRCODE", "native")
        self.mode.setToolTip(
            "Raster : tout est envoyé en image (accents, polices TrueType, rotations exactes).\n"
            "Natif : commandes texte de l'imprimante (très léger, polices internes, sans caractères exotiques)."
        )
        self.copies = QSpinBox()
        self.copies.setRange(1, 999)

        form.addRow("Format prédéfini", self.preset)
        form.addRow("Unité", self.unit)
        form.addRow("Largeur", self.width)
        form.addRow("Hauteur", self.height)
        form.addRow("Support", self.media)
        form.addRow("Espacement", self.gap)
        form.addRow("Résolution", self.dpi)
        form.addRow("Noirceur", self.density)
        form.addRow("Vitesse", self.speed)
        form.addRow("DIRECTION", self.direction)
        form.addRow("Langage", self.language)
        form.addRow("Mode d'impression", self.mode)
        form.addRow("Copies", self.copies)

        self.preset.activated.connect(self._apply_preset)
        self.unit.currentIndexChanged.connect(self._unit_changed)
        for w in (self.width, self.height, self.gap, self.density, self.speed, self.copies):
            w.valueChanged.connect(self._push)
        for w in (self.media, self.dpi, self.direction, self.language, self.mode):
            w.currentIndexChanged.connect(self._push)
        self._configure_spins()

    # ------------------------------------------------------------ unités
    def _unit_is_dots(self) -> bool:
        return self.unit.currentData() == "dots"

    def _factor(self) -> float:
        """Valeur affichée = mm × facteur."""
        return self.settings.dots_per_mm if self._unit_is_dots() else 1.0

    def _configure_spins(self) -> None:
        dots = self._unit_is_dots()
        for w in (self.width, self.height):
            w.setRange(5, 2400 if dots else 300)
            w.setDecimals(0 if dots else 1)
            w.setSingleStep(8 if dots else 1)
            w.setSuffix(" pt" if dots else " mm")

    def _show_size(self) -> None:
        f = self._factor()
        self.width.setValue(self.settings.width_mm * f)
        self.height.setValue(self.settings.height_mm * f)

    def _unit_changed(self) -> None:
        self._loading = True
        self._configure_spins()
        self._show_size()
        self._loading = False

    def _apply_preset(self, index: int) -> None:
        size = self.preset.itemData(index)
        if size:
            self.settings.width_mm, self.settings.height_mm = size
            self._loading = True
            self._show_size()
            self._loading = False
            self.changed.emit()

    # ------------------------------------------------------------ modèle <-> widgets
    def load(self, s: LabelSettings) -> None:
        self._loading = True
        self.settings = s
        self._configure_spins()
        self._show_size()
        self.media.setCurrentIndex(max(0, self.media.findData(s.media)))
        self.gap.setValue(s.gap_mm)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(s.dpi)))
        self.density.setValue(s.density)
        self.speed.setValue(s.speed)
        self.direction.setCurrentIndex(max(0, self.direction.findData(s.direction)))
        self.language.setCurrentIndex(max(0, self.language.findData(s.language)))
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.mode)))
        self.copies.setValue(s.copies)
        match = next((i for i in range(1, self.preset.count()) if self.preset.itemData(i) == (s.width_mm, s.height_mm)), 0)
        self.preset.setCurrentIndex(match)
        self.gap.setEnabled(s.media != "continuous")
        self._loading = False

    def _push(self, *_args) -> None:
        if self._loading:
            return
        s = self.settings
        s.dpi = self.dpi.currentData()  # avant la taille : le facteur « points » en dépend
        f = self._factor()
        s.width_mm = round(self.width.value() / f, 2)
        s.height_mm = round(self.height.value() / f, 2)
        s.media = self.media.currentData()
        s.gap_mm = self.gap.value()
        s.density, s.speed = self.density.value(), self.speed.value()
        s.direction = self.direction.currentData()
        s.language = self.language.currentData()
        s.mode = self.mode.currentData()
        s.copies = self.copies.value()
        self.gap.setEnabled(s.media != "continuous")
        self._loading = True
        match = next((i for i in range(1, self.preset.count()) if self.preset.itemData(i) == (s.width_mm, s.height_mm)), 0)
        self.preset.setCurrentIndex(match)
        self._loading = False
        self.changed.emit()
