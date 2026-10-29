"""Fenêtre principale : liste d'éléments, aperçu, panneaux de réglages, commandes générées, journal."""

from __future__ import annotations

import copy
import os
from datetime import datetime

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import __version__, importer, printer, templates
from ..imaging import encode_image_file
from ..models import KIND_LABELS, Element, Label
from ..renderer import render_label, warnings_for
from ..tspl import build_tspl
from ..zpl import build_zpl
from .canvas import LabelCanvas
from .connection_panel import ConnectionPanel
from .element_panel import ElementPanel
from .import_dialog import ImportDialog
from .label_panel import LabelPanel
from .pairing_dialog import PairingDialog
from .workers import Worker

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)"
PRINTER_CHECK_MS = 60_000  # vérification périodique de la joignabilité de l'imprimante

NEW_ELEMENT_DEFAULTS = {
    "text": dict(text="Nouveau texte", font_size=5.0),
    "barcode": dict(text="123456789012", symbology="code128", bar_height=12.0, module=2),
    "qrcode": dict(text="https://exemple.fr", qr_cell=5),
    "image": dict(width=25.0),
    "rect": dict(width=40.0, height=20.0, thickness=0.4),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phomemo Label Studio")
        self.resize(1400, 900)
        self.setAcceptDrops(True)  # glisser un PDF / une image d'étiquette sur la fenêtre
        self.label: Label = templates.shipping_100x150()
        self.path: str | None = None
        self.dirty = False
        self._worker: Worker | None = None

        # ---- zone centrale : aperçu dans une zone défilante
        self.canvas = LabelCanvas(self.label)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.scroll.setStyleSheet("QScrollArea { background: #9aa0a6; }")
        self.setCentralWidget(self.scroll)

        # ---- panneaux
        self.label_panel = LabelPanel()
        self.element_panel = ElementPanel()
        self.conn_panel = ConnectionPanel()
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._scrolled(self.label_panel), "Étiquette")
        self.right_tabs.addTab(self._scrolled(self.element_panel), "Élément")
        self.right_tabs.addTab(self._scrolled(self.conn_panel), "Connexion")
        right = QDockWidget("Réglages", self)
        right.setWidget(self.right_tabs)
        right.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        right.setMinimumWidth(340)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, right)

        self.elements_list = QListWidget()
        left = QDockWidget("Éléments", self)
        left.setWidget(self._build_elements_dock())
        left.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        left.setMinimumWidth(230)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left)

        self.code_view = QPlainTextEdit()
        self.code_view.setReadOnly(True)
        self.code_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.code_view.setStyleSheet("font-family: monospace;")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.code_view, "Commandes générées")
        self.bottom_tabs.addTab(self.log_view, "Journal")
        bottom = QDockWidget("Sortie", self)
        bottom.setWidget(self.bottom_tabs)
        bottom.setMinimumHeight(150)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, bottom)
        self.bottom_dock = bottom

        self.printer_state = QLabel("○ Imprimante : inconnue")
        self.printer_state.setStyleSheet("color: gray; padding: 0 8px;")
        self.printer_state.setToolTip("État de la liaison, vérifié automatiquement toutes les minutes.")
        self.statusBar().addPermanentWidget(self.printer_state)
        self._printer_ok: bool | None = None
        self._probe: Worker | None = None

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)

        self._build_actions()

        # ---- signaux
        self.canvas.selectionChanged.connect(self._on_canvas_selected)
        self.canvas.elementMoved.connect(self.element_panel.sync_position)
        self.canvas.editFinished.connect(self._after_edit)
        self.canvas.deleteRequested.connect(self.delete_element)
        self.canvas.zoomRequested.connect(lambda k: self._set_zoom(self.canvas.px_per_mm * k))
        self.elements_list.currentRowChanged.connect(self._on_list_row)
        self.element_panel.changed.connect(self._element_changed)
        self.label_panel.changed.connect(self._label_changed)
        self.conn_panel.detectRequested.connect(self.detect_printer)
        self.conn_panel.pairRequested.connect(self.pair_printer)
        self.conn_panel.forgetRequested.connect(self.forget_printer)
        self.conn_panel.testRequested.connect(self.test_connection)
        self.conn_panel.selftestRequested.connect(self.selftest)

        # La génération des commandes est regroupée (anti-rebond) : elle refait un rendu complet
        self._code_timer = QTimer(self, singleShot=True, interval=350)
        self._code_timer.timeout.connect(self._update_code_view)

        # Vérification périodique de l'imprimante : en arrière-plan, jamais pendant un envoi
        self._probe_timer = QTimer(self, interval=PRINTER_CHECK_MS)
        self._probe_timer.timeout.connect(self.check_printer)

        self._load_label(self.label, fit=True)
        self.log(f"Phomemo Label Studio {__version__} démarré.")
        self._probe_timer.start()
        QTimer.singleShot(1200, self.check_printer)

    # ================================================================== construction
    @staticmethod
    def _scrolled(widget: QWidget) -> QScrollArea:
        # Le ressort empêche le formulaire de s'étirer sur toute la hauteur du panneau
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.addWidget(widget)
        lay.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(holder)
        return area

    def _build_elements_dock(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        for kind in ("text", "barcode", "qrcode", "image", "rect"):
            b = QPushButton(f"＋ {KIND_LABELS[kind]}")
            b.clicked.connect(lambda _=False, k=kind: self.add_element(k))
            lay.addWidget(b)
        lay.addWidget(self.elements_list, 1)
        row = QHBoxLayout()
        for text, slot, tip in (("▲", lambda: self.move_element(-1), "Monter (passer derrière)"),
                                ("▼", lambda: self.move_element(1), "Descendre (passer devant)"),
                                ("⧉", self.duplicate_element, "Dupliquer"),
                                ("🗑", self.delete_element, "Supprimer")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        return box

    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            return a

        a_new = act("Nouveau", self.new_label, "Ctrl+N")
        a_open = act("Ouvrir…", self.open_file, "Ctrl+O")
        a_save = act("Enregistrer", self.save_file, "Ctrl+S")
        a_saveas = act("Enregistrer sous…", lambda: self.save_file(as_new=True), "Ctrl+Shift+S")
        a_import = act("Importer des étiquettes Vinted / Leboncoin…", lambda: self.import_shipping(), "Ctrl+I",
                       "Rogner et imprimer un bordereau PDF ou une image")
        a_png = act("Exporter l'aperçu (PNG)…", self.export_png)
        a_cmd = act("Exporter les commandes brutes…", self.export_commands)
        a_quit = act("Quitter", self.close, "Ctrl+Q")
        self.a_print = act("🖨 Imprimer", self.print_label, "Ctrl+P")
        self.a_detect = act("🔍 Détecter l'imprimante", self.detect_printer, "Ctrl+D",
                            "Cherche l'imprimante Bluetooth / USB / série et teste la liaison")
        self.a_test = act("Tester la connexion", self.test_connection)
        a_zin = act("Zoom +", lambda: self._set_zoom(self.canvas.px_per_mm * 1.25), "Ctrl++")
        a_zout = act("Zoom −", lambda: self._set_zoom(self.canvas.px_per_mm / 1.25), "Ctrl+-")
        a_fit = act("Ajuster", self.fit_zoom, "Ctrl+0")
        a_about = act("À propos", self.about)

        m = self.menuBar()
        mf = m.addMenu("&Fichier")
        for a in (a_new, a_open, a_save, a_saveas):
            mf.addAction(a)
        mf.addSeparator()
        mf.addAction(a_import)
        mf.addSeparator()
        for a in (a_png, a_cmd):
            mf.addAction(a)
        mf.addSeparator()
        mf.addAction(a_quit)
        mt = m.addMenu("&Modèles")
        for name, factory in templates.TEMPLATES.items():
            mt.addAction(act(name, lambda _=False, f=factory: self._confirm_discard() and self._load_label(f(), fit=True)))
        mv = m.addMenu("&Affichage")
        for a in (a_zin, a_zout, a_fit):
            mv.addAction(a)
        mi = m.addMenu("&Imprimante")
        mi.addAction(self.a_detect)
        mi.addAction(self.a_test)
        mi.addAction(self.a_print)
        m.addMenu("&Aide").addAction(a_about)

        tb = QToolBar("Principal")
        tb.setMovable(False)
        self.addToolBar(tb)
        for a in (a_new, a_open, a_save):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(a_import)
        tb.addSeparator()
        for a in (a_zout, a_zin, a_fit):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_detect)
        tb.addAction(self.a_test)
        tb.addAction(self.a_print)

    # ================================================================== modèle <-> interface
    def _load_label(self, label: Label, fit: bool = False, path: str | None = None) -> None:
        self.label = label
        self.path = path
        self.canvas.set_label(label)
        self.label_panel.load(label.settings)
        self._rebuild_list()
        self.element_panel.load(None)
        self._set_dirty(False)
        if fit:
            QTimer.singleShot(0, self.fit_zoom)
        self._update_code_view()

    def _rebuild_list(self, select: int = -1) -> None:
        self.elements_list.blockSignals(True)
        self.elements_list.clear()
        for el in self.label.elements:
            self.elements_list.addItem(el.display_name())
        self.elements_list.setCurrentRow(select)
        self.elements_list.blockSignals(False)

    def _selected_index(self) -> int:
        return self.canvas.selected if 0 <= self.canvas.selected < len(self.label.elements) else -1

    def _select(self, index: int) -> None:
        self.canvas.set_selected(index)
        self.elements_list.blockSignals(True)
        self.elements_list.setCurrentRow(index)
        self.elements_list.blockSignals(False)
        self.element_panel.load(self.label.elements[index] if index >= 0 else None)
        if index >= 0:
            self.right_tabs.setCurrentIndex(1)

    def _on_canvas_selected(self, index: int) -> None:
        self._select(index)

    def _on_list_row(self, row: int) -> None:
        self._select(row)

    def _element_changed(self) -> None:
        self.canvas.refresh()
        row = self._selected_index()
        if row >= 0 and self.elements_list.item(row):
            self.elements_list.item(row).setText(self.label.elements[row].display_name())
        self._set_dirty(True)
        self._code_timer.start()

    def _after_edit(self) -> None:
        """Fin d'un déplacement à la souris / au clavier."""
        self._set_dirty(True)
        self._code_timer.start()

    def _label_changed(self) -> None:
        self.canvas.refresh()
        self._set_dirty(True)
        self._code_timer.start()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        name = os.path.basename(self.path) if self.path else "sans titre"
        self.setWindowTitle(f"{'● ' if dirty else ''}{name} — Phomemo Label Studio")

    # ================================================================== éléments
    def add_element(self, kind: str) -> None:
        n = len(self.label.elements)
        el = Element(kind=kind, x=5.0 + (n % 6) * 3, y=5.0 + (n % 6) * 3, **NEW_ELEMENT_DEFAULTS[kind])
        if kind == "image":
            path, _ = QFileDialog.getOpenFileName(self, "Choisir une image", "", IMAGE_FILTER)
            if not path:
                return
            try:
                el.image_b64 = encode_image_file(path)
            except Exception as exc:
                QMessageBox.warning(self, "Image illisible", str(exc))
                return
            el.image_name = os.path.basename(path)
        self.label.elements.append(el)
        self._rebuild_list(len(self.label.elements) - 1)
        self.canvas.refresh()
        self._select(len(self.label.elements) - 1)
        self._set_dirty(True)
        self._code_timer.start()

    def delete_element(self) -> None:
        i = self._selected_index()
        if i < 0:
            return
        del self.label.elements[i]
        new = min(i, len(self.label.elements) - 1)
        self._rebuild_list(new)
        self.canvas.refresh()
        self._select(new)
        self._set_dirty(True)
        self._code_timer.start()

    def duplicate_element(self) -> None:
        i = self._selected_index()
        if i < 0:
            return
        clone = copy.deepcopy(self.label.elements[i])
        clone.x, clone.y = clone.x + 3, clone.y + 3
        self.label.elements.insert(i + 1, clone)
        self._rebuild_list(i + 1)
        self.canvas.refresh()
        self._select(i + 1)
        self._set_dirty(True)
        self._code_timer.start()

    def move_element(self, delta: int) -> None:
        i = self._selected_index()
        j = i + delta
        if i < 0 or not 0 <= j < len(self.label.elements):
            return
        els = self.label.elements
        els[i], els[j] = els[j], els[i]
        self._rebuild_list(j)
        self.canvas.refresh()
        self._select(j)
        self._set_dirty(True)
        self._code_timer.start()

    # ================================================================== zoom
    def _set_zoom(self, px_per_mm: float) -> None:
        self.canvas.set_zoom(px_per_mm)

    def fit_zoom(self) -> None:
        s = self.label.settings
        avail = self.scroll.viewport().size()
        k = min((avail.width() - 60) / s.width_mm, (avail.height() - 60) / s.height_mm)
        self._set_zoom(max(1.0, k))

    # ================================================================== fichiers
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(
            self, "Modifications non enregistrées", "Abandonner les modifications en cours ?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return r == QMessageBox.StandardButton.Discard

    def new_label(self) -> None:
        if self._confirm_discard():
            s = self.label.settings
            self._load_label(templates.blank(s.width_mm, s.height_mm), fit=True)
            self.label.settings = copy.deepcopy(s)
            self.label_panel.load(self.label.settings)
            self.canvas.refresh()

    def open_file(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir une étiquette", "", "Étiquettes (*.label.json *.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                label = Label.from_json(fh.read())
        except Exception as exc:
            QMessageBox.critical(self, "Fichier invalide", f"Impossible de lire {path} :\n{exc}")
            return
        self._load_label(label, fit=True, path=path)
        self.log(f"Ouvert : {path}")

    def save_file(self, as_new: bool = False) -> None:
        path = self.path
        if as_new or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Enregistrer l'étiquette", "etiquette.label.json",
                                                  "Étiquettes (*.label.json)")
            if not path:
                return
            if not path.endswith(".json"):
                path += ".label.json"
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.label.to_json())
        except OSError as exc:
            QMessageBox.critical(self, "Échec de l'enregistrement", str(exc))
            return
        self.path = path
        self._set_dirty(False)
        self.log(f"Enregistré : {path}")

    def export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exporter l'aperçu", "etiquette.png", "PNG (*.png)")
        if path:
            image, _ = render_label(self.label)
            image.convert("1").save(path)
            self.log(f"Aperçu exporté : {path}")

    def export_commands(self) -> None:
        ext = "zpl" if self.label.settings.language == "zpl" else "tspl"
        path, _ = QFileDialog.getSaveFileName(self, "Exporter les commandes", f"etiquette.{ext}", f"Commandes (*.{ext})")
        if path:
            with open(path, "wb") as fh:
                fh.write(self._build(preview=False))
            self.log(f"Commandes exportées : {path}")

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Opération en cours", "Attendez la fin de l'envoi à l'imprimante.")
            event.ignore()
        elif self._confirm_discard():
            self._probe_timer.stop()
            if self._probe and self._probe.isRunning():
                self._probe.wait(4000)  # laisse la vérification en cours se terminer
            event.accept()
        else:
            event.ignore()

    # ================================================================== commandes / impression
    def _build(self, preview: bool) -> bytes:
        builder = build_zpl if self.label.settings.language == "zpl" else build_tspl
        return builder(self.label, preview=preview)

    def _update_code_view(self) -> None:
        text = self._build(preview=True).decode("utf-8" if self.label.settings.language == "zpl" else "cp1252",
                                                "replace")
        self.code_view.setPlainText(text)

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")
        self.statusBar().showMessage(message.splitlines()[0], 8000)

    def _run(self, fn, busy_text: str, on_ok, failure_title: str = "Erreur d'impression",
             inspect_commands: bool = True) -> None:
        """Lance `fn` en arrière-plan ; `failure_title` et l'onglet affiché dépendent de l'opération."""
        if self._worker and self._worker.isRunning():
            self.log("Une opération est déjà en cours.")
            return
        self.log(busy_text)
        self.conn_panel.set_busy(True)
        self.a_print.setEnabled(False)
        self.a_detect.setEnabled(False)
        self.a_test.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        w = Worker(fn, self)
        w.progress.connect(self.progress.setValue)
        w.message.connect(self.log)
        w.succeeded.connect(on_ok)
        w.failed.connect(lambda message: self._on_failed(message, failure_title, inspect_commands))
        w.finished.connect(self._on_worker_done)
        w.finished.connect(w.deleteLater)  # sans cela, un QThread s'accumule à chaque opération
        self._worker = w
        w.start()

    def _on_worker_done(self) -> None:
        self._worker = None  # le thread est détruit juste après : plus aucune référence
        self.progress.setVisible(False)
        self.conn_panel.set_busy(False)
        self.a_print.setEnabled(True)
        self.a_detect.setEnabled(True)
        self.a_test.setEnabled(True)

    def _on_failed(self, message: str, title: str = "Erreur d'impression", inspect_commands: bool = True) -> None:
        self.log(f"Erreur : {message}")
        self.conn_panel.set_status(message, ok=False)
        if inspect_commands:
            self.bottom_tabs.setCurrentIndex(1)
        QMessageBox.critical(self, title, message)

    def print_label(self) -> None:
        cfg = self.conn_panel.config()
        _, rendered = render_label(self.label)
        problems = warnings_for(rendered)
        if problems:
            r = QMessageBox.warning(
                self, "Éléments non imprimables",
                "Ces éléments seront remplacés par un cadre barré (aperçu) ou ignorés (mode natif) :\n\n• "
                + "\n• ".join(problems) + "\n\nImprimer quand même ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        data = self._build(preview=False)
        lang = self.label.settings.language.upper()

        def job(progress):
            printer.send_job(cfg, data, progress)
            return len(data)

        def ok(size):
            self.log(f"Impression envoyée ({lang}, {size} octets).")
            self.conn_panel.set_status("Dernier envoi : OK", ok=True)
            self._set_printer_state(True, "Dernier envoi réussi.", announce=False)

        self._run(job, f"Envoi de {len(data)} octets ({lang}) via {cfg['type']}…", ok)

    def pair_printer(self) -> None:
        """Recherche et appaire une imprimante Bluetooth, puis bascule la connexion dessus."""
        dlg = PairingDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.mac:
            return
        self.conn_panel.use_bluetooth(dlg.mac)
        self.log(f"Imprimante Bluetooth appairée : {dlg.mac}")
        self.test_connection()

    def forget_printer(self) -> None:
        """Désappaire l'appareil Bluetooth choisi dans l'onglet Connexion, après confirmation."""
        mac, name = self.conn_panel.selected_paired()
        if not mac:
            self.conn_panel.set_status("Aucun appareil appairé à oublier : choisissez-en un dans la liste "
                                       "ou saisissez son adresse MAC.", ok=False)
            return
        label = name or mac
        answer = QMessageBox.question(
            self, "Oublier un appareil Bluetooth",
            f"Retirer {label} ({mac}) des appareils appairés ?\n\n"
            "L'imprimante devra être appairée de nouveau (bouton « Appairer… ») pour être utilisée "
            "en Bluetooth.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def ok(result: printer.ForgetResult) -> None:
            self.log(("✔ " if result.ok else "✘ ") + result.message)
            self.conn_panel.set_status(result.message, ok=result.ok)
            if not result.ok:
                return
            self.conn_panel.remove_paired(mac)
            self.conn_panel.refresh_devices()
            cfg = self.conn_panel.config()
            if cfg["type"] == "bluetooth" and not printer.MAC_RE.match(cfg.get("mac", "")):
                self._set_printer_state(None, "Imprimante Bluetooth oubliée : appairez-la de nouveau.")

        self._run(lambda _p: printer.forget_bluetooth(mac, name), f"Oubli de {label}…", ok,
                  failure_title="Oubli de l'appareil Bluetooth", inspect_commands=False)

    def detect_printer(self) -> None:
        """Cherche l'imprimante (Bluetooth appairée, USB, série), remplit la connexion et teste la liaison."""
        lang = self.label.settings.language

        def ok(detection):
            for device in detection.devices:
                self.log("• " + device.label)
            self.log(detection.summary())
            if detection.result is not None:
                self.log(("✔ " if detection.result.ok else "✘ ") + detection.result.message)
            if detection.chosen is not None and not self._confirm_replacement(self.conn_panel.config(),
                                                                             detection.chosen):
                self.log("Détection ignorée : la connexion actuelle est conservée.")
                self.conn_panel.set_status("Détection ignorée : la connexion actuelle est conservée.")
                return
            self.conn_panel.apply_detection(detection)

        self._run(lambda progress, message: printer.detect_printer(progress, message, lang),
                  "Recherche de l'imprimante…", ok,
                  failure_title="Détection de l'imprimante", inspect_commands=False)

    @staticmethod
    def _connection_key(cfg: dict) -> tuple[str, str]:
        """Identité d'une connexion : mode + adresse MAC ou chemin du port."""
        return (cfg.get("type") or "", (cfg.get("mac") or cfg.get("path") or "").strip().upper())

    @staticmethod
    def _describe_connection(cfg: dict) -> str:
        kind, address = MainWindow._connection_key(cfg)
        return {"bluetooth": f"Bluetooth {address}", "usb": f"USB {address}",
                "serial": f"port série {address}"}.get(kind, kind or "non configurée")

    def _confirm_replacement(self, cfg: dict, device: printer.Device) -> bool:
        """Vrai si la détection peut remplacer la connexion : vide, identique, injoignable, ou acceptée."""
        current = self._connection_key(cfg)
        if not current[1] or current == self._connection_key(device.config):
            return True
        if self._printer_ok is False:  # imprimante déjà constatée injoignable : la remplacer est utile
            return True
        answer = QMessageBox.question(
            self, "Remplacer la connexion ?",
            f"La connexion actuelle — {self._describe_connection(cfg)} — est conservée par défaut.\n\n"
            f"La remplacer par {device.label} ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def test_connection(self) -> None:
        cfg, lang = self.conn_panel.config(), self.label.settings.language

        def ok(result):
            self.log(("✔ " if result.ok else "✘ ") + result.message)
            self.conn_panel.set_status(result.message, ok=result.ok)
            self._set_printer_state(result.ok, result.message, announce=False)

        self._run(lambda _p: printer.test_connection(cfg, lang), "Test de la connexion…", ok,
                  failure_title="Test de la connexion", inspect_commands=False)

    # ================================================================== état de l'imprimante
    def check_printer(self) -> None:
        """Vérifie en arrière-plan que l'imprimante répond, sans jamais gêner un envoi en cours."""
        if (self._worker and self._worker.isRunning()) or (self._probe and self._probe.isRunning()):
            return
        cfg, lang = self.conn_panel.config(), self.label.settings.language
        if cfg["type"] == "bluetooth" and not printer.MAC_RE.match(cfg.get("mac", "")):
            self._set_printer_state(None, "Choisissez l'imprimante dans l'onglet Connexion.")
            return
        if cfg["type"] in ("usb", "serial") and not cfg.get("path"):
            self._set_printer_state(None, "Choisissez le port dans l'onglet Connexion.")
            return
        probe = Worker(lambda _progress: printer.test_connection(cfg, lang, timeout=3.0), self)
        probe.succeeded.connect(lambda result, probed=cfg: self._on_probe(result, probed))
        probe.finished.connect(self._probe_finished)
        probe.finished.connect(probe.deleteLater)
        self._probe = probe
        probe.start()

    def _on_probe(self, result: printer.TestResult, cfg: dict) -> None:
        if cfg != self.conn_panel.config():
            return  # la connexion a changé (oubli, détection…) : ce résultat ne la concerne plus
        self._set_printer_state(result.ok, result.message)

    def _probe_finished(self) -> None:
        self._probe = None

    def _set_printer_state(self, ok: bool | None, detail: str = "", announce: bool = True) -> None:
        """Met à jour l'indicateur permanent ; ne journalise que les changements d'état."""
        text = {True: "Imprimante : joignable", False: "Imprimante : injoignable",
                None: "Imprimante : non configurée"}[ok]
        color = {True: "#188038", False: "#d93025", None: "gray"}[ok]
        self.printer_state.setText(f"{'●' if ok is not None else '○'} {text}")
        self.printer_state.setStyleSheet(f"color: {color}; padding: 0 8px;")
        self.printer_state.setToolTip(detail or text)
        if ok is not None and ok != self._printer_ok and announce:
            self.log(("✔ " if ok else "✘ ") + (detail.splitlines()[0] if detail else text))
        if ok is not None:
            self._printer_ok = ok

    def selftest(self) -> None:
        cfg, lang = self.conn_panel.config(), self.label.settings.language

        def ok(_):
            self.log("Auto-test envoyé : l'imprimante doit sortir sa page de configuration.")

        self._run(lambda _p: printer.send_selftest(cfg, lang), "Envoi de l'auto-test…", ok)

    # ================================================================== import Vinted / Leboncoin
    def import_shipping(self, paths: list[str] | None = None) -> None:
        """Rogne un ou plusieurs bordereaux (PDF/images), puis charge ou imprime le résultat."""
        dlg = ImportDialog(self.label.settings, paths, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.element is None:
            return
        if dlg.print_now and len(dlg.jobs) > 1:  # impression par lot : l'éditeur n'est pas modifié
            self.print_batch(dlg.jobs)
            return
        if dlg.replace:
            if not self._confirm_discard():
                return
            settings = copy.deepcopy(self.label.settings)
            settings.width_mm, settings.height_mm = dlg.target_size
            self._load_label(Label(settings, [dlg.element]), fit=True)
        else:
            self.label.elements.append(dlg.element)
            self._rebuild_list(len(self.label.elements) - 1)
            self.canvas.refresh()
            self._select(len(self.label.elements) - 1)
        self._set_dirty(True)
        self._code_timer.start()
        self.log(f"Étiquette de transport importée : {dlg.element.image_name}")
        if dlg.print_now:
            QTimer.singleShot(0, self.print_label)

    def print_batch(self, jobs: list) -> None:
        """Imprime plusieurs étiquettes à la suite sur une seule connexion (confirmation avant envoi)."""
        n = len(jobs)
        r = QMessageBox.question(
            self, "Imprimer le lot",
            f"Envoyer {n} étiquettes à l'imprimante ?\n\nFormat : {jobs[0][1][0]:g} × {jobs[0][1][1]:g} mm, "
            f"{self.label.settings.copies} exemplaire(s) chacune.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        cfg = self.conn_panel.config()
        base = copy.deepcopy(self.label.settings)
        builder = build_zpl if base.language == "zpl" else build_tspl

        def frames():
            for el, (w_mm, h_mm) in jobs:  # chaque trame est rendue juste avant son envoi
                s = copy.deepcopy(base)
                s.width_mm, s.height_mm = w_mm, h_mm
                yield builder(Label(s, [el]))

        def job(progress, message):
            return printer.send_jobs(
                cfg, frames(), progress, total=n,
                on_job=lambda i, total: message(f"Étiquette {i}/{total}…"),
            )

        def ok(sent):
            self.log(f"Lot terminé : {sent} étiquette(s) envoyée(s).")
            self.conn_panel.set_status(f"Dernier lot : {sent} étiquette(s) OK", ok=True)

        self._run(job, f"Impression de {n} étiquettes via {cfg['type']}…", ok)

    def dragEnterEvent(self, event) -> None:
        if self._dropped_files(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        files = self._dropped_files(event)
        if files:
            self.import_shipping(files)

    @staticmethod
    def _dropped_files(event) -> list[str]:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        return sorted(p for p in paths if p.lower().endswith(importer.SUPPORTED_EXTS))

    # ================================================================== divers
    def about(self) -> None:
        QMessageBox.about(
            self, "À propos",
            f"<b>Phomemo Label Studio {__version__}</b><br>Conception et impression d'étiquettes thermiques "
            "(TSPL / ZPL) pour Phomemo PM-241-BT.<br><br>Bluetooth RFCOMM, USB et série — sans CUPS.",
        )
