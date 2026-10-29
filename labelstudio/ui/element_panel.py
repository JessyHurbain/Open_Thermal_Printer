"""Panneau de propriétés de l'élément sélectionné (champs dynamiques selon le type)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QWidget,
)

from ..imaging import DITHER_MODES, encode_image_file
from ..models import KIND_ICONS, KIND_LABELS, SYMBOLOGIES, Element


def _dspin(lo: float, hi: float, step: float, suffix: str = " mm", decimals: int = 1) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    w.setSuffix(suffix)
    return w


class ElementPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.element: Element | None = None
        self._loading = False
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.rows: dict[str, list[QWidget]] = {}  # type d'élément -> champs visibles pour ce type

        self.title = QLabel("Aucun élément sélectionné")
        self.title.setStyleSheet("font-weight: 600;")
        self.form.addRow(self.title)

        # --- commun
        self.name = QLineEdit()
        self.name.setPlaceholderText("(facultatif)")
        self.x = _dspin(-500, 1000, 0.5)
        self.y = _dspin(-500, 1000, 0.5)
        self.rotation = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotation.addItem(f"{deg}°", deg)
        self._row("Nom", self.name, "all")
        self._row("Position X", self.x, "all")
        self._row("Position Y", self.y, "all")
        self._row("Rotation (horaire)", self.rotation, "text", "barcode", "qrcode", "image", "rect")

        # --- contenu (texte / code-barres / QR)
        self.content = QPlainTextEdit()
        self.content.setFixedHeight(80)
        self.content_label = QLabel("Contenu")
        self.form.addRow(self.content_label, self.content)
        self._register(self.content, ("text", "barcode", "qrcode"))

        # --- texte
        self.font_family = QFontComboBox()
        self.font_size = _dspin(1, 100, 0.5)
        self.bold = QCheckBox("Gras")
        self.italic = QCheckBox("Italique")
        style_box = QWidget()
        style_lay = QHBoxLayout(style_box)
        style_lay.setContentsMargins(0, 0, 0, 0)
        style_lay.addWidget(self.bold)
        style_lay.addWidget(self.italic)
        self.align = QComboBox()
        for key, txt in (("left", "Gauche"), ("center", "Centré"), ("right", "Droite")):
            self.align.addItem(txt, key)
        self._row("Police", self.font_family, "text")
        self._row("Taille (hauteur)", self.font_size, "text")
        self._row("Style", style_box, "text")
        self._row("Alignement", self.align, "text")

        # --- code-barres
        self.symbology = QComboBox()
        for key, txt in SYMBOLOGIES.items():
            self.symbology.addItem(txt, key)
        self.bar_height = _dspin(2, 200, 1)
        self.module = QSpinBox()
        self.module.setRange(1, 10)
        self.module.setSuffix(" pt")
        self.module.setToolTip("Largeur de la barre étroite en points imprimante (1 pt ≈ 0,125 mm à 203 dpi)")
        self.human = QCheckBox("Afficher le texte sous le code")
        self._row("Symbologie", self.symbology, "barcode")
        self._row("Hauteur des barres", self.bar_height, "barcode")
        self._row("Largeur de module", self.module, "barcode")
        self._row("", self.human, "barcode")

        # --- QR
        self.qr_cell = QSpinBox()
        self.qr_cell.setRange(1, 20)
        self.qr_cell.setSuffix(" pt")
        self.qr_ecc = QComboBox()
        for key, txt in (("L", "L — 7 %"), ("M", "M — 15 %"), ("Q", "Q — 25 %"), ("H", "H — 30 %")):
            self.qr_ecc.addItem(txt, key)
        self._row("Taille d'un module", self.qr_cell, "qrcode")
        self._row("Correction d'erreur", self.qr_ecc, "qrcode")

        # --- image
        self.pick_btn = QPushButton("Choisir une image…")
        self.pick_btn.clicked.connect(self._pick_image)
        self.img_name = QLabel("—")
        self.img_width = _dspin(2, 400, 1)
        self.dither = QComboBox()
        for key, txt in DITHER_MODES.items():
            self.dither.addItem(txt, key)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(1, 254)
        self.invert = QCheckBox("Inverser noir / blanc")
        self._row("Fichier", self.pick_btn, "image")
        self._row("", self.img_name, "image")
        self._row("Largeur", self.img_width, "image")
        self._row("Conversion N&B", self.dither, "image")
        self._row("Seuil (luminosité)", self.threshold, "image")
        self._row("", self.invert, "image")

        # --- rectangle
        self.rect_w = _dspin(0.2, 400, 1)
        self.rect_h = _dspin(0.2, 600, 1)
        self.thickness = _dspin(0.1, 20, 0.1)
        self.filled = QCheckBox("Rempli (trait plein)")
        self._row("Largeur", self.rect_w, "rect")
        self._row("Hauteur", self.rect_h, "rect")
        self._row("Épaisseur du trait", self.thickness, "rect")
        self._row("", self.filled, "rect")

        # --- signaux
        for w in (self.name,):
            w.textChanged.connect(self._push)
        self.content.textChanged.connect(self._push)
        for w in (self.x, self.y, self.font_size, self.bar_height, self.img_width, self.rect_w, self.rect_h,
                  self.thickness):
            w.valueChanged.connect(self._push)
        for w in (self.module, self.qr_cell, self.threshold):
            w.valueChanged.connect(self._push)
        for w in (self.rotation, self.align, self.symbology, self.qr_ecc, self.dither):
            w.currentIndexChanged.connect(self._push)
        self.font_family.currentFontChanged.connect(self._push)
        for w in (self.bold, self.italic, self.human, self.invert, self.filled):
            w.toggled.connect(self._push)

        self.load(None)

    # ------------------------------------------------------------ construction
    def _row(self, label: str, widget: QWidget, *kinds: str) -> None:
        lab = QLabel(label)
        self.form.addRow(lab, widget)
        self._register(widget, kinds)

    def _register(self, widget: QWidget, kinds: tuple[str, ...]) -> None:
        for kind in kinds:
            self.rows.setdefault(kind, []).append(widget)

    def _show_for(self, kind: str | None) -> None:
        """Affiche uniquement les lignes (libellé + champ) pertinentes pour ce type d'élément."""
        everything = {w for ws in self.rows.values() for w in ws}
        visible = set(self.rows.get("all", [])) | set(self.rows.get(kind, [])) if kind else set()
        for w in everything:
            self.form.setRowVisible(w, w in visible)

    # ------------------------------------------------------------ modèle <-> widgets
    def load(self, el: Element | None) -> None:
        self._loading = True
        self.element = el
        self._show_for(el.kind if el else None)
        self.setEnabled(el is not None)
        if el is None:
            self.title.setText("Aucun élément sélectionné")
            self._loading = False
            return
        self.title.setText(f"{KIND_ICONS.get(el.kind, '')} {KIND_LABELS.get(el.kind, el.kind)}".strip())
        self.name.setText(el.name)
        self.x.setValue(el.x)
        self.y.setValue(el.y)
        self.rotation.setCurrentIndex(max(0, self.rotation.findData(el.rotation)))
        self.content_label.setText({"text": "Texte", "barcode": "Données", "qrcode": "Données / URL"}.get(el.kind, ""))
        self.content.setPlainText(el.text)
        self.font_family.setCurrentFont(QFont(el.font_family))
        self.font_size.setValue(el.font_size)
        self.bold.setChecked(el.bold)
        self.italic.setChecked(el.italic)
        self.align.setCurrentIndex(max(0, self.align.findData(el.align)))
        self.symbology.setCurrentIndex(max(0, self.symbology.findData(el.symbology)))
        self.bar_height.setValue(el.bar_height)
        self.module.setValue(el.module)
        self.human.setChecked(el.human_readable)
        self.qr_cell.setValue(el.qr_cell)
        self.qr_ecc.setCurrentIndex(max(0, self.qr_ecc.findData(el.qr_ecc)))
        self.img_name.setText(el.image_name or "(aucune image)")
        self.img_width.setValue(el.width)
        self.dither.setCurrentIndex(max(0, self.dither.findData(el.dither)))
        self.threshold.setValue(el.threshold)
        self.invert.setChecked(el.invert)
        self.rect_w.setValue(el.width)
        self.rect_h.setValue(el.height)
        self.thickness.setValue(el.thickness)
        self.filled.setChecked(el.filled)
        self._loading = False

    def sync_position(self) -> None:
        """Met à jour X / Y quand l'élément est déplacé à la souris."""
        if self.element is None:
            return
        self._loading = True
        self.x.setValue(self.element.x)
        self.y.setValue(self.element.y)
        self._loading = False

    def _push(self, *_args) -> None:
        el = self.element
        if el is None or self._loading:
            return
        el.name = self.name.text()
        el.x, el.y = self.x.value(), self.y.value()
        el.rotation = self.rotation.currentData()
        text = self.content.toPlainText()
        el.text = text.replace("\n", "") if el.kind == "barcode" else text
        el.font_family = self.font_family.currentFont().family()
        el.font_size = self.font_size.value()
        el.bold, el.italic = self.bold.isChecked(), self.italic.isChecked()
        el.align = self.align.currentData()
        el.symbology = self.symbology.currentData()
        el.bar_height = self.bar_height.value()
        el.module = self.module.value()
        el.human_readable = self.human.isChecked()
        el.qr_cell = self.qr_cell.value()
        el.qr_ecc = self.qr_ecc.currentData()
        el.dither = self.dither.currentData()
        el.threshold = self.threshold.value()
        el.invert = self.invert.isChecked()
        if el.kind == "image":
            el.width = self.img_width.value()
        elif el.kind == "rect":
            el.width, el.height = self.rect_w.value(), self.rect_h.value()
        el.thickness = self.thickness.value()
        el.filled = self.filled.isChecked()
        self.changed.emit()

    def _pick_image(self) -> None:
        if self.element is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir une image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)"
        )
        if not path:
            return
        try:
            self.element.image_b64 = encode_image_file(path)
        except Exception as exc:
            self.img_name.setText(f"Erreur : {exc}")
            return
        self.element.image_name = path.rsplit("/", 1)[-1]
        self.img_name.setText(self.element.image_name)
        self.changed.emit()
