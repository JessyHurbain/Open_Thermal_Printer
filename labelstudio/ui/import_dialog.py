"""Boîte de dialogue « Importer une étiquette de transport » (Vinted, Leboncoin, etc.).

Ouvre un PDF ou une image, propose un rognage automatique, laisse l'ajuster à la souris,
mémorise la mise en page par transporteur et montre l'étiquette thermique finale.
"""

from __future__ import annotations

import copy
import os

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from .. import importer
from ..imaging import encode_pil_image
from ..models import Element, Label, LabelSettings
from ..renderer import render_label
from .label_panel import PRESETS


class CropView(QWidget):
    """Page affichée à l'échelle ; blocs détectés en pointillés, rognage courant en bleu."""

    rectChanged = pyqtSignal(tuple)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(420, 560)
        self.setMouseTracking(True)
        self._img: Image.Image | None = None
        self._qimg: QImage | None = None
        self._buf = b""
        self.blocks: list[importer.Rect] = []
        self.crop: importer.Rect | None = None
        self._drag_start: QPointF | None = None
        self._drag_now: QPointF | None = None

    def set_image(self, img: Image.Image | None) -> None:
        self._img = img
        if img is None:
            self._qimg = None
        else:
            self._buf = img.tobytes()
            self._qimg = QImage(self._buf, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        self.blocks, self.crop = [], None
        self.update()

    # --- géométrie image <-> widget
    def _geometry(self) -> tuple[float, float, float]:
        if self._img is None:
            return 1.0, 0.0, 0.0
        s = min(self.width() / self._img.width, self.height() / self._img.height)
        return s, (self.width() - self._img.width * s) / 2, (self.height() - self._img.height * s) / 2

    def _to_img(self, p: QPointF) -> tuple[float, float]:
        s, ox, oy = self._geometry()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def _to_widget(self, r: importer.Rect) -> QRectF:
        s, ox, oy = self._geometry()
        return QRectF(ox + r[0] * s, oy + r[1] * s, (r[2] - r[0]) * s, (r[3] - r[1]) * s)

    # --- dessin
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self._bounds(), QColor("#9aa0a6"))
        if self._qimg is None:
            p.setPen(QColor("white"))
            p.drawText(self._bounds(), Qt.AlignmentFlag.AlignCenter, "Ouvrez un PDF ou une image\n(ou glissez-le ici)")
            return
        s, ox, oy = self._geometry()
        target = QRectF(ox, oy, self._img.width * s, self._img.height * s)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(target, self._qimg)

        if self.crop is not None:  # assombrit tout sauf le rognage
            keep = self._to_widget(self.crop)
            shade = QColor(0, 0, 0, 110)
            p.fillRect(QRectF(target.left(), target.top(), target.width(), keep.top() - target.top()), shade)
            p.fillRect(QRectF(target.left(), keep.bottom(), target.width(), target.bottom() - keep.bottom()), shade)
            p.fillRect(QRectF(target.left(), keep.top(), keep.left() - target.left(), keep.height()), shade)
            p.fillRect(QRectF(keep.right(), keep.top(), target.right() - keep.right(), keep.height()), shade)

        p.setPen(QPen(QColor("#ea8600"), 1, Qt.PenStyle.DashLine))
        for b in self.blocks:
            p.drawRect(self._to_widget(b))
        if self.crop is not None:
            p.setPen(QPen(QColor("#1a73e8"), 2))
            p.drawRect(self._to_widget(self.crop))
        if self._drag_start is not None and self._drag_now is not None:
            p.setPen(QPen(QColor("#1a73e8"), 1, Qt.PenStyle.DotLine))
            p.drawRect(QRectF(self._drag_start, self._drag_now).normalized())
        p.end()

    def _bounds(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    # --- souris : clic sur un bloc = le choisir ; glisser = rectangle libre
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if self._img is not None and e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._drag_now = e.position()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_start is not None:
            self._drag_now = e.position()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag_start is None or self._img is None:
            return
        a, b = self._drag_start, e.position()
        self._drag_start = self._drag_now = None
        if abs(a.x() - b.x()) < 5 and abs(a.y() - b.y()) < 5:  # simple clic
            x, y = self._to_img(b)
            hit = [r for r in self.blocks if r[0] <= x <= r[2] and r[1] <= y <= r[3]]
            if hit:  # le plus petit bloc contenant le clic
                self.set_rect(min(hit, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])))
            self.update()
            return
        x0, y0 = self._to_img(a)
        x1, y1 = self._to_img(b)
        W, H = self._img.size
        rect = (int(max(0, min(x0, x1))), int(max(0, min(y0, y1))),
                int(min(W, max(x0, x1))), int(min(H, max(y0, y1))))
        if rect[2] - rect[0] > 8 and rect[3] - rect[1] > 8:
            self.set_rect(rect)
        self.update()

    def set_rect(self, rect: importer.Rect | None) -> None:
        self.crop = rect
        self.update()
        if rect is not None:
            self.rectChanged.emit(rect)


class ImportDialog(QDialog):
    def __init__(self, current: LabelSettings, path: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Importer une étiquette de transport (Vinted, Leboncoin…)")
        self.resize(1100, 760)
        self.qs = QSettings("PhomemoLabelStudio", "LabelStudio")
        self.base = copy.deepcopy(current)
        self.source: importer.PageSource | None = None
        self.page_img: Image.Image | None = None
        self.turns = 0  # rotation manuelle (quarts de tour horaires)
        self.presets = importer.load_presets(self.qs.value("import/presets", ""))

        # résultats lus par la fenêtre principale
        self.element: Element | None = None
        self.target_size: tuple[float, float] = (current.width_mm, current.height_mm)
        self.replace = True
        self.print_now = False

        root = QHBoxLayout(self)
        self.view = CropView()
        root.addWidget(self.view, 3)

        side = QVBoxLayout()
        root.addLayout(side, 2)

        # --- source
        box = QGroupBox("1. Document")
        f = QVBoxLayout(box)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Ouvrir un PDF / une image…")
        self.page_spin = QSpinBox()
        self.page_spin.setPrefix("Page ")
        self.page_spin.setMinimum(1)
        row.addWidget(self.open_btn, 1)
        row.addWidget(self.page_spin)
        f.addLayout(row)
        self.file_label = QLabel("Aucun fichier")
        self.file_label.setStyleSheet("color: gray;")
        f.addWidget(self.file_label)
        side.addWidget(box)

        # --- rognage
        box = QGroupBox("2. Rognage")
        f = QVBoxLayout(box)
        hint = QLabel("Cliquez sur un bloc orange, ou tracez un rectangle à la souris.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        f.addWidget(hint)
        row = QHBoxLayout()
        self.auto_btn = QPushButton("Détection auto")
        self.tight_btn = QPushButton("Retirer les marges")
        self.rot_btn = QPushButton("↻ 90°")
        for b in (self.auto_btn, self.tight_btn, self.rot_btn):
            row.addWidget(b)
        f.addLayout(row)
        row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.apply_preset_btn = QPushButton("Appliquer")
        self.save_preset_btn = QPushButton("Mémoriser…")
        self.del_preset_btn = QPushButton("✕")
        self.del_preset_btn.setToolTip("Supprimer ce préréglage")
        row.addWidget(self.preset_combo, 1)
        for b in (self.apply_preset_btn, self.save_preset_btn, self.del_preset_btn):
            row.addWidget(b)
        f.addLayout(row)
        side.addWidget(box)

        # --- mise en page
        box = QGroupBox("3. Mise en page sur l'étiquette thermique")
        form = QFormLayout(box)
        self.format_combo = QComboBox()
        self.format_combo.addItem(f"Format actuel ({current.width_mm:g} × {current.height_mm:g} mm)",
                                  (current.width_mm, current.height_mm))
        for name, size in PRESETS.items():
            self.format_combo.addItem(name, size)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0, 20)
        self.margin.setSuffix(" mm")
        self.margin.setValue(float(self.qs.value("import/margin", 2.0)))
        self.auto_rotate = QCheckBox("Tourner automatiquement si l'étiquette est plus grande ainsi")
        self.auto_rotate.setChecked(True)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(60, 230)
        self.threshold.setValue(int(self.qs.value("import/threshold", 160)))
        self.threshold.setToolTip("Plus haut = plus noir. Les codes-barres exigent des traits bien noirs.")
        self.dither = QComboBox()
        self.dither.addItem("Seuil (recommandé : texte et codes-barres)", "threshold")
        self.dither.addItem("Floyd-Steinberg (logos en dégradés)", "floyd")
        form.addRow("Format", self.format_combo)
        form.addRow("Marge", self.margin)
        form.addRow("", self.auto_rotate)
        form.addRow("Noirceur", self.threshold)
        form.addRow("Conversion", self.dither)
        side.addWidget(box)

        # --- aperçu final
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(250)
        self.preview.setStyleSheet("background: #9aa0a6;")
        side.addWidget(self.preview, 1)

        self.replace_check = QCheckBox("Remplacer l'étiquette courante (sinon : ajouter à l'étiquette en cours)")
        self.replace_check.setChecked(True)
        side.addWidget(self.replace_check)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Ajouter")
        self.print_btn = QPushButton("🖨 Importer et imprimer")
        self.print_btn.setDefault(True)
        cancel = QPushButton("Annuler")
        row.addStretch(1)
        for b in (cancel, self.add_btn, self.print_btn):
            row.addWidget(b)
        side.addLayout(row)

        # --- signaux
        self.open_btn.clicked.connect(self._browse)
        self.page_spin.valueChanged.connect(self._load_page)
        self.auto_btn.clicked.connect(self._auto)
        self.tight_btn.clicked.connect(self._tighten)
        self.rot_btn.clicked.connect(self._rotate)
        self.apply_preset_btn.clicked.connect(self._apply_preset)
        self.save_preset_btn.clicked.connect(self._save_preset)
        self.del_preset_btn.clicked.connect(self._delete_preset)
        self.view.rectChanged.connect(lambda _r: self._schedule_preview())
        for w in (self.format_combo, self.dither):
            w.currentIndexChanged.connect(self._schedule_preview)
        self.margin.valueChanged.connect(self._schedule_preview)
        self.threshold.valueChanged.connect(self._schedule_preview)
        self.auto_rotate.toggled.connect(self._schedule_preview)
        self.replace_check.toggled.connect(self._replace_toggled)
        self.add_btn.clicked.connect(lambda: self._finish(False))
        self.print_btn.clicked.connect(lambda: self._finish(True))
        cancel.clicked.connect(self.reject)

        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._update_preview)
        self._refresh_presets()
        self._set_actions_enabled(False)
        if path:
            QTimer.singleShot(0, lambda: self.load_file(path))

    # ------------------------------------------------------------ chargement
    def _browse(self) -> None:
        start = str(self.qs.value("import/folder", os.path.expanduser("~/Téléchargements")))
        path, _ = QFileDialog.getOpenFileName(
            self, "Étiquette de transport", start,
            "Étiquettes (*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;Tous les fichiers (*)",
        )
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        try:
            src = importer.PageSource(path)
        except importer.ImportError_ as exc:
            QMessageBox.warning(self, "Import impossible", str(exc))
            return
        if self.source:
            self.source.close()
        self.source = src
        self.qs.setValue("import/folder", os.path.dirname(path))
        self.file_label.setText(os.path.basename(path))
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(src.page_count)
        self.page_spin.setValue(1)
        self.page_spin.setVisible(src.page_count > 1)
        self.page_spin.blockSignals(False)
        self.turns = 0
        self._load_page()

    def _load_page(self) -> None:
        if not self.source:
            return
        self.page_img = self.source.render(self.page_spin.value() - 1)
        self.view.set_image(self.page_img)
        self._set_actions_enabled(True)
        self._auto()

    def _set_actions_enabled(self, on: bool) -> None:
        for b in (self.auto_btn, self.tight_btn, self.rot_btn, self.apply_preset_btn, self.save_preset_btn,
                  self.add_btn, self.print_btn):
            b.setEnabled(on)

    # ------------------------------------------------------------ rognage
    def _auto(self) -> None:
        if self.page_img is None:
            return
        self.view.blocks = importer.find_blocks(self.page_img)
        self.view.set_rect(importer.auto_crop(self.page_img))

    def _tighten(self) -> None:
        if self.page_img is not None and self.view.crop:
            self.view.set_rect(importer.tighten(self.page_img, self.view.crop))

    def _rotate(self) -> None:
        self.turns = (self.turns + 1) % 4
        self._schedule_preview()

    # ------------------------------------------------------------ préréglages (par transporteur)
    def _refresh_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItems(sorted(self.presets))
        has = bool(self.presets)
        self.apply_preset_btn.setEnabled(has and self.page_img is not None)
        self.del_preset_btn.setEnabled(has)
        self.preset_combo.setEnabled(has)

    def _save_preset(self) -> None:
        if self.page_img is None or self.view.crop is None:
            return
        name, ok = QInputDialog.getText(
            self, "Mémoriser ce rognage", "Nom (ex. « Vinted – Mondial Relay ») :",
            text=self.preset_combo.currentText() if self.presets else "",
        )
        name = name.strip()
        if ok and name:
            self.presets[name] = {"rect": importer.rect_to_fractions(self.view.crop, self.page_img.size),
                                  "turns": self.turns}
            self.qs.setValue("import/presets", importer.dump_presets(self.presets))
            self._refresh_presets()
            self.preset_combo.setCurrentText(name)

    def _apply_preset(self) -> None:
        preset = self.presets.get(self.preset_combo.currentText())
        if preset and self.page_img is not None:
            self.turns = int(preset.get("turns", 0)) % 4
            self.view.blocks = importer.find_blocks(self.page_img)
            self.view.set_rect(importer.fractions_to_rect(preset["rect"], self.page_img.size))

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name in self.presets:
            del self.presets[name]
            self.qs.setValue("import/presets", importer.dump_presets(self.presets))
            self._refresh_presets()

    # ------------------------------------------------------------ aperçu / résultat
    def _replace_toggled(self, on: bool) -> None:
        self.format_combo.setEnabled(on)
        if not on:  # on ajoute à l'étiquette en cours : son format ne change pas
            self.format_combo.setCurrentIndex(0)
        self.add_btn.setText("Remplacer" if on else "Ajouter")
        self._schedule_preview()

    def _schedule_preview(self, *_a) -> None:
        self._timer.start()

    def _build_element(self) -> tuple[Element, tuple[float, float]] | None:
        if self.page_img is None or self.view.crop is None:
            return None
        w_mm, h_mm = self.format_combo.currentData()
        crop = self.page_img.crop(self.view.crop)
        place = importer.fit_to_label(crop, w_mm, h_mm, self.margin.value(), self.auto_rotate.isChecked(),
                                      self.turns * 90)
        el = Element(
            kind="image", name="Étiquette transport", x=place.x_mm, y=place.y_mm, width=place.width_mm,
            image_b64=encode_pil_image(place.image),
            image_name=self.file_label.text(), threshold=self.threshold.value(), dither=self.dither.currentData(),
        )
        return el, (w_mm, h_mm)

    def _update_preview(self) -> None:
        built = self._build_element()
        if built is None:
            self.preview.clear()
            return
        el, (w_mm, h_mm) = built
        s = copy.deepcopy(self.base)
        s.width_mm, s.height_mm = w_mm, h_mm
        img, _ = render_label(Label(s, [el]))
        rgb = img.convert("RGB")
        data = rgb.tobytes()
        qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
        box = self.preview.size()
        pix = QPixmap.fromImage(qimg).scaled(box.width() - 12, box.height() - 12, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _finish(self, print_now: bool) -> None:
        built = self._build_element()
        if built is None:
            return
        self.element, self.target_size = built
        self.replace = self.replace_check.isChecked()
        self.print_now = print_now
        self.qs.setValue("import/margin", self.margin.value())
        self.qs.setValue("import/threshold", self.threshold.value())
        self.accept()

    def done(self, code: int) -> None:
        if self.source:
            self.source.close()
        super().done(code)
