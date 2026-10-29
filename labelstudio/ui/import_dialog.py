"""Boîte de dialogue « Importer une étiquette de transport » (Vinted, Leboncoin, etc.).

Ouvre un PDF ou une image, propose un rognage automatique, laisse l'ajuster à la souris,
mémorise la mise en page par transporteur et montre l'étiquette thermique finale.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
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




@dataclass
class QueueItem:
    """Une page à imprimer : son rognage est mémorisé en fractions de la page (indépendant de la résolution)."""

    path: str
    page: int  # 0-based
    crop_fr: list[float] | None = None
    turns: int = 0
    note: str = ""

    @property
    def title(self) -> str:
        return os.path.basename(self.path) + (f"  (p. {self.page + 1})" if self.page else "")


class ImportDialog(QDialog):
    """File d'attente de bordereaux : chaque page est rognée (auto, préréglage ou à la main) puis imprimée."""

    def __init__(self, current: LabelSettings, paths: list[str] | str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Importer des étiquettes de transport (Vinted, Leboncoin…)")
        self.resize(1150, 800)
        self.qs = QSettings("PhomemoLabelStudio", "LabelStudio")
        self.base = copy.deepcopy(current)
        self.presets = importer.load_presets(self.qs.value("import/presets", ""))

        self.items: list[QueueItem] = []
        self.cur = -1
        self.sources: dict[str, importer.PageSource] = {}
        self._pages: dict[tuple[str, int], Image.Image] = {}  # petit cache de pages rendues
        self._keywords: dict[tuple[str, int], list[str]] = {}
        self._module_cache: dict = {}

        # résultats lus par la fenêtre principale
        self.element: Element | None = None  # étiquette sélectionnée (Remplacer / Ajouter)
        self.jobs: list[tuple[Element, tuple[float, float]]] = []  # toute la file (impression par lot)
        self.target_size: tuple[float, float] = (current.width_mm, current.height_mm)
        self.replace = True
        self.print_now = False

        root = QHBoxLayout(self)
        self.view = CropView()
        root.addWidget(self.view, 3)
        side = QVBoxLayout()
        root.addLayout(side, 2)

        # --- 1. file d'attente
        box = QGroupBox("1. Documents")
        f = QVBoxLayout(box)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Ajouter des PDF / images…")
        self.remove_btn = QPushButton("Retirer")
        self.clear_btn = QPushButton("Vider")
        row.addWidget(self.open_btn, 1)
        row.addWidget(self.remove_btn)
        row.addWidget(self.clear_btn)
        f.addLayout(row)
        self.queue = QListWidget()
        self.queue.setMaximumHeight(110)
        f.addWidget(self.queue)
        self.detect_label = QLabel("Aucun fichier. Glissez des PDF dans la fenêtre ou utilisez le bouton.")
        self.detect_label.setWordWrap(True)
        self.detect_label.setStyleSheet("color: gray;")
        f.addWidget(self.detect_label)
        side.addWidget(box)

        # --- 2. rognage
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
        self.apply_all_btn = QPushButton("À toute la file")
        self.save_preset_btn = QPushButton("Mémoriser…")
        self.del_preset_btn = QPushButton("✕")
        self.del_preset_btn.setToolTip("Supprimer ce préréglage")
        row.addWidget(self.preset_combo, 1)
        for b in (self.apply_preset_btn, self.apply_all_btn, self.save_preset_btn, self.del_preset_btn):
            row.addWidget(b)
        f.addLayout(row)
        side.addWidget(box)

        # --- 3. mise en page
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
        self.optimize = QCheckBox("Optimiser pour les codes-barres (barres sur un nombre entier de points)")
        self.optimize.setChecked(self.qs.value("import/optimize", "true") in (True, "true"))
        self.module_info = QLabel("")
        self.module_info.setStyleSheet("color: gray;")
        self.module_info.setWordWrap(True)
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
        form.addRow("", self.optimize)
        form.addRow("", self.module_info)
        form.addRow("Noirceur", self.threshold)
        form.addRow("Conversion", self.dither)
        side.addWidget(box)

        # --- aperçu final
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet("background: #9aa0a6;")
        side.addWidget(self.preview, 1)

        self.replace_check = QCheckBox("Remplacer l'étiquette courante (sinon : ajouter à l'étiquette en cours)")
        self.replace_check.setChecked(True)
        side.addWidget(self.replace_check)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Remplacer")
        self.print_btn = QPushButton("🖨 Importer et imprimer")
        self.print_btn.setDefault(True)
        cancel = QPushButton("Annuler")
        row.addStretch(1)
        for b in (cancel, self.add_btn, self.print_btn):
            row.addWidget(b)
        side.addLayout(row)

        # --- signaux
        self.open_btn.clicked.connect(self._browse)
        self.remove_btn.clicked.connect(self._remove_item)
        self.clear_btn.clicked.connect(self._clear)
        self.queue.currentRowChanged.connect(self._select_item)
        self.auto_btn.clicked.connect(self._auto)
        self.tight_btn.clicked.connect(self._tighten)
        self.rot_btn.clicked.connect(self._rotate)
        self.apply_preset_btn.clicked.connect(lambda: self._apply_preset(self.cur))
        self.apply_all_btn.clicked.connect(self._apply_preset_to_all)
        self.save_preset_btn.clicked.connect(self._save_preset)
        self.del_preset_btn.clicked.connect(self._delete_preset)
        self.view.rectChanged.connect(self._on_rect_changed)
        for w in (self.format_combo, self.dither):
            w.currentIndexChanged.connect(self._schedule_preview)
        self.margin.valueChanged.connect(self._schedule_preview)
        self.threshold.valueChanged.connect(self._schedule_preview)
        self.auto_rotate.toggled.connect(self._schedule_preview)
        self.optimize.toggled.connect(self._schedule_preview)
        self.replace_check.toggled.connect(self._replace_toggled)
        self.add_btn.clicked.connect(lambda: self._finish(False))
        self.print_btn.clicked.connect(lambda: self._finish(True))
        cancel.clicked.connect(self.reject)

        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._update_preview)
        self._refresh_presets()
        self._refresh_buttons()
        if paths:
            files = [paths] if isinstance(paths, str) else list(paths)
            QTimer.singleShot(0, lambda: self.add_files(files))

    # ------------------------------------------------------------ file d'attente
    def _browse(self) -> None:
        start = str(self.qs.value("import/folder", os.path.expanduser("~/Téléchargements")))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Étiquettes de transport", start,
            "Étiquettes (*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;Tous les fichiers (*)",
        )
        if paths:
            self.add_files(paths)

    def add_files(self, paths: list[str]) -> None:
        """Ajoute chaque page de chaque fichier à la file (un PDF de 3 pages = 3 étiquettes)."""
        first_new = len(self.items)
        for path in paths:
            try:
                src = self.sources.get(path) or importer.PageSource(path)
            except importer.ImportError_ as exc:
                QMessageBox.warning(self, "Import impossible", f"{os.path.basename(path)} : {exc}")
                continue
            self.sources[path] = src
            self.qs.setValue("import/folder", os.path.dirname(path))
            for page in range(src.page_count):
                self.items.append(QueueItem(path, page))
        self._refresh_queue()
        if len(self.items) > first_new:
            self.queue.setCurrentRow(first_new)

    def _remove_item(self) -> None:
        row = self.cur
        if 0 <= row < len(self.items):
            del self.items[row]
            self.cur = -1
            self._refresh_queue()
            if self.items:
                self.queue.setCurrentRow(min(row, len(self.items) - 1))
            else:
                self.view.set_image(None)
                self.preview.clear()
                self.detect_label.setText("File vide.")

    def _clear(self) -> None:
        self.items.clear()
        self.cur = -1
        self._refresh_queue()
        self.view.set_image(None)
        self.preview.clear()

    def _refresh_queue(self) -> None:
        self.queue.blockSignals(True)
        self.queue.clear()
        for i, item in enumerate(self.items, 1):
            self.queue.addItem(f"{i}. {item.title}")
        self.queue.blockSignals(False)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        n, has = len(self.items), bool(self.items)
        for b in (self.auto_btn, self.tight_btn, self.rot_btn, self.save_preset_btn, self.add_btn, self.print_btn,
                  self.remove_btn, self.clear_btn):
            b.setEnabled(has)
        usable = has and bool(self.presets)
        self.apply_preset_btn.setEnabled(usable)
        self.apply_all_btn.setEnabled(usable and n > 1)
        self.print_btn.setText("🖨 Importer et imprimer" if n <= 1 else f"🖨 Imprimer les {n} étiquettes")
        self.add_btn.setText(("Remplacer" if self.replace_check.isChecked() else "Ajouter")
                             + ("" if n <= 1 else " (celle-ci)"))

    # ------------------------------------------------------------ pages et rognage
    def _item_page(self, item: QueueItem) -> Image.Image:
        key = (item.path, item.page)
        if key not in self._pages:
            if len(self._pages) >= 4:  # on ne garde que quelques pages A4 à 300 dpi en mémoire
                self._pages.pop(next(iter(self._pages)))
            self._pages[key] = self.sources[item.path].render(item.page)
        return self._pages[key]

    def _item_keywords(self, item: QueueItem) -> list[str]:
        key = (item.path, item.page)
        if key not in self._keywords:
            self._keywords[key] = importer.detect_keywords(self.sources[item.path].page_text(item.page))
        return self._keywords[key]

    def _ensure_crop(self, item: QueueItem) -> None:
        """Rognage initial : préréglage reconnu automatiquement, sinon détection du plus grand bloc."""
        if item.crop_fr is not None:
            return
        page = self._item_page(item)
        keywords = self._item_keywords(item)
        name = importer.match_preset(self.presets, keywords, page.size)
        if name:
            preset = self.presets[name]
            item.turns = int(preset.get("turns", 0)) % 4
            item.crop_fr = list(preset["rect"])
            item.note = f"{importer.keywords_label(keywords)} détecté → préréglage « {name} » appliqué automatiquement."
        else:
            texts = self.sources[item.path].text_blocks(item.page)
            item.crop_fr = importer.rect_to_fractions(importer.auto_crop(page, texts), page.size)
            if keywords:
                item.note = (f"{importer.keywords_label(keywords)} détecté, mais aucun préréglage mémorisé : "
                             "rognage automatique. Ajustez puis « Mémoriser… » pour la prochaine fois.")
            elif self.sources[item.path].is_pdf:
                item.note = "Transporteur non reconnu : rognage automatique du plus grand bloc."
            else:
                item.note = "Image : rognage automatique du plus grand bloc."

    def _select_item(self, row: int) -> None:
        if not 0 <= row < len(self.items):
            return
        self.cur = row
        item = self.items[row]
        page = self._item_page(item)
        self._ensure_crop(item)
        self.view.set_image(page)
        self.view.blocks = importer.find_blocks(page)
        self.detect_label.setText(item.note)
        self.view.crop = importer.fractions_to_rect(item.crop_fr, page.size)
        self.view.update()
        self._refresh_presets()
        self._update_preview()

    def _on_rect_changed(self, rect: tuple) -> None:
        if 0 <= self.cur < len(self.items):
            self.items[self.cur].crop_fr = importer.rect_to_fractions(rect, self._item_page(self.items[self.cur]).size)
        self._schedule_preview()

    def _auto(self) -> None:
        if self.cur < 0:
            return
        item = self.items[self.cur]
        page = self._item_page(item)
        self.view.blocks = importer.find_blocks(page)
        self.view.set_rect(importer.auto_crop(page, self.sources[item.path].text_blocks(item.page)))

    def _tighten(self) -> None:
        if self.cur >= 0 and self.view.crop:
            self.view.set_rect(importer.tighten(self._item_page(self.items[self.cur]), self.view.crop))

    def _rotate(self) -> None:
        if self.cur >= 0:
            self.items[self.cur].turns = (self.items[self.cur].turns + 1) % 4
            self._schedule_preview()

    # ------------------------------------------------------------ préréglages (par transporteur)
    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(sorted(self.presets))
        if current in self.presets:
            self.preset_combo.setCurrentText(current)
        self.preset_combo.blockSignals(False)
        has = bool(self.presets)
        self.del_preset_btn.setEnabled(has)
        self.preset_combo.setEnabled(has)
        n, any_items = len(self.items), bool(self.items)
        self.apply_preset_btn.setEnabled(has and any_items)
        self.apply_all_btn.setEnabled(has and n > 1)

    def _save_preset(self) -> None:
        if self.cur < 0 or self.view.crop is None:
            return
        item = self.items[self.cur]
        page = self._item_page(item)
        keywords = self._item_keywords(item)
        suggestion = importer.keywords_label(keywords).replace(" + ", " – ") or self.preset_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "Mémoriser ce rognage",
            "Nom du préréglage (il sera appliqué automatiquement aux documents de même transporteur "
            f"et de même format{'' if keywords else ' — aucun transporteur détecté ici, il faudra donc le choisir à la main'}) :",
            text=suggestion,
        )
        name = name.strip()
        if ok and name:
            self.presets[name] = {
                "rect": importer.rect_to_fractions(self.view.crop, page.size), "turns": item.turns,
                "match": keywords, "ratio": round(page.width / page.height, 4),
            }
            self.qs.setValue("import/presets", importer.dump_presets(self.presets))
            self._refresh_presets()
            self.preset_combo.setCurrentText(name)
            self._refresh_buttons()

    def _apply_preset(self, index: int) -> None:
        preset = self.presets.get(self.preset_combo.currentText())
        if preset and 0 <= index < len(self.items):
            item = self.items[index]
            item.turns = int(preset.get("turns", 0)) % 4
            item.crop_fr = list(preset["rect"])
            item.note = f"Préréglage « {self.preset_combo.currentText()} » appliqué."
            if index == self.cur:
                self._select_item(index)

    def _apply_preset_to_all(self) -> None:
        for i in range(len(self.items)):
            self._apply_preset(i)

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name in self.presets:
            del self.presets[name]
            self.qs.setValue("import/presets", importer.dump_presets(self.presets))
            self._refresh_presets()
            self._refresh_buttons()

    # ------------------------------------------------------------ aperçu / résultat
    def _replace_toggled(self, on: bool) -> None:
        self.format_combo.setEnabled(on)
        if not on:  # on ajoute à l'étiquette en cours : son format ne change pas
            self.format_combo.setCurrentIndex(0)
        self._refresh_buttons()
        self._schedule_preview()

    def _schedule_preview(self, *_a) -> None:
        self._timer.start()

    def _element_for(self, item: QueueItem) -> tuple[Element, tuple[float, float], importer.Placement]:
        self._ensure_crop(item)
        page = self._item_page(item)
        rect = importer.fractions_to_rect(item.crop_fr, page.size)
        w_mm, h_mm = self.format_combo.currentData()
        place = importer.build_placement(
            self.sources[item.path], item.page, page, rect, w_mm, h_mm, self.margin.value(),
            self.auto_rotate.isChecked(), item.turns * 90, self.base.dpi, self.optimize.isChecked(),
            self._module_cache, cache_key=(item.path, item.page),
        )
        el = Element(
            kind="image", name="Étiquette transport", x=place.x_mm, y=place.y_mm, width=place.width_mm,
            image_b64=encode_pil_image(place.image), image_name=item.title,
            threshold=self.threshold.value(), dither=self.dither.currentData(),
        )
        return el, (w_mm, h_mm), place

    def _update_preview(self) -> None:
        if not 0 <= self.cur < len(self.items):
            self.preview.clear()
            return
        el, (w_mm, h_mm), place = self._element_for(self.items[self.cur])
        if not self.optimize.isChecked():
            self.module_info.setText("")
        elif place.module_dots:
            self.module_info.setText(f"Barre étroite calée sur {place.module_dots} pt (taille d'origine × {place.scale / 25.4 * 300:.2f}).")
        else:
            self.module_info.setText("Pas de code-barres repéré, ou aucun calage entier sans trop réduire l'étiquette.")
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
        if not self.items or self.cur < 0:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.jobs = []
            if print_now and len(self.items) > 1:  # impression par lot : toute la file
                for item in self.items:
                    el, size, _ = self._element_for(item)
                    self.jobs.append((el, size))
                self.element, self.target_size = self.jobs[min(self.cur, len(self.jobs) - 1)]
            else:
                self.element, self.target_size, _ = self._element_for(self.items[self.cur])
        finally:
            QApplication.restoreOverrideCursor()
        self.replace = self.replace_check.isChecked()
        self.print_now = print_now
        self.qs.setValue("import/margin", self.margin.value())
        self.qs.setValue("import/threshold", self.threshold.value())
        self.qs.setValue("import/optimize", self.optimize.isChecked())
        self.accept()

    def done(self, code: int) -> None:
        for src in self.sources.values():
            src.close()
        super().done(code)
