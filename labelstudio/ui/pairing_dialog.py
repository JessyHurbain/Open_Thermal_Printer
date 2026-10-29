"""Boîte de dialogue « Appairer une imprimante Bluetooth » : recherche, appairage et approbation, sans terminal."""

from __future__ import annotations

from PyQt6.QtCore import QRegularExpression, QSettings, Qt, QTimer
from PyQt6.QtGui import QFont, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from .. import printer
from .workers import Worker


class PairingDialog(QDialog):
    """Cherche les appareils Bluetooth à portée, appaire celui qui est choisi et l'approuve (trust)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Appairer une imprimante Bluetooth")
        self.resize(560, 440)
        self.mac: str | None = None  # adresse retenue, lue par la fenêtre principale après acceptation
        self._worker: Worker | None = None
        self._accepted = False
        self._settings = QSettings("PhomemoLabelStudio", "LabelStudio")

        root = QVBoxLayout(self)
        hint = QLabel("Allumez l'imprimante et mettez-la en mode appairage, puis lancez la recherche. "
                      "L'application appaire l'appareil choisi et l'approuve, en envoyant le code PIN "
                      "ci-dessous (0000 par défaut).")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        pin_row = QHBoxLayout()
        pin_row.addWidget(QLabel("Code PIN"))
        self.pin = QLineEdit(str(self._settings.value("pair/pin", "0000")))
        self.pin.setMaxLength(6)
        self.pin.setFixedWidth(70)
        self.pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pin.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,6}"), self))
        self.pin.setToolTip("Code demandé par l'imprimante : 0000 le plus souvent, parfois 1234")
        pin_row.addWidget(self.pin)
        pin_hint = QLabel("0000 le plus souvent, parfois 1234")
        pin_hint.setStyleSheet("color: gray;")
        pin_row.addWidget(pin_hint)
        pin_row.addStretch(1)
        root.addLayout(pin_row)

        row = QHBoxLayout()
        self.scan_btn = QPushButton("Rechercher")
        self.pair_btn = QPushButton("Appairer")
        self.pair_btn.setEnabled(False)
        close = QPushButton("Fermer")
        row.addWidget(self.scan_btn)
        row.addWidget(self.pair_btn)
        row.addStretch(1)
        row.addWidget(close)
        root.addLayout(row)

        self.status = QLabel("Prêt.")
        self.status.setWordWrap(True)
        self._set_status("Prêt.")

        root.addWidget(self.status)

        self.scan_btn.clicked.connect(self.scan)
        self.pair_btn.clicked.connect(self.pair)
        self.list.currentRowChanged.connect(lambda _row: self._sync_buttons())
        self.list.itemDoubleClicked.connect(lambda _item: self.pair())
        close.clicked.connect(self.reject)

        QTimer.singleShot(0, self.scan)  # première recherche dès l'ouverture

    # ------------------------------------------------------------ état
    def _sync_buttons(self) -> None:
        busy = self._accepted or bool(self._worker and self._worker.isRunning())
        self.scan_btn.setEnabled(not busy)
        self.pair_btn.setEnabled(not busy and self.list.currentItem() is not None)
        self.list.setEnabled(not busy)
        self.pin.setEnabled(not busy)

    def _set_status(self, text: str, tone: str = "gray") -> None:
        self.status.setStyleSheet(f"color: {tone};")
        self.status.setText(text)

    def _start(self, fn, done) -> None:
        if self._worker and self._worker.isRunning():
            return
        worker = Worker(fn, self)
        worker.succeeded.connect(done)
        worker.failed.connect(lambda message: self._set_status(message, "#d93025"))
        worker.finished.connect(self._worker_done)
        worker.finished.connect(worker.deleteLater)  # sinon un QThread s'accumule à chaque recherche
        self._worker = worker
        self._sync_buttons()
        worker.start()

    def _worker_done(self) -> None:
        self._worker = None  # le thread est détruit juste après : plus aucune référence
        QTimer.singleShot(0, self._sync_buttons)

    # ------------------------------------------------------------ recherche
    def scan(self) -> None:
        """Recherche les appareils à portée (scan Bluetooth borné) sans bloquer la fenêtre."""
        self._set_status("Recherche des appareils Bluetooth à portée… (quelques secondes)")
        self._start(lambda progress, message: printer.discover_bluetooth_devices(), self._show_devices)

    def _show_devices(self, devices: list[tuple[str, str]]) -> None:
        paired = {mac for mac, _name in printer.list_paired_devices()}
        self.list.clear()
        probable: QListWidgetItem | None = None
        for mac, name in devices:
            text = f"{name or 'appareil sans nom'}  ({mac})"
            if mac in paired:
                text += "  — déjà appairé"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, mac)
            item.setData(Qt.ItemDataRole.UserRole + 1, name)
            if printer.looks_like_printer(name):
                item.setText(text + "  — imprimante probable")
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                probable = probable or item
            self.list.addItem(item)
        if not devices:
            self._set_status("Aucun appareil trouvé : vérifiez que l'imprimante est allumée, en mode "
                             "appairage et à portée, puis relancez la recherche.", "#d93025")
            return
        self.list.setCurrentItem(probable or self.list.item(0))
        self._set_status(f"{len(devices)} appareil(s) trouvé(s). Choisissez l'imprimante, puis « Appairer ».")
        self._sync_buttons()

    # ------------------------------------------------------------ appairage
    def pair(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        mac = item.data(Qt.ItemDataRole.UserRole)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        pin = self.pin.text().strip() or "0000"
        self._settings.setValue("pair/pin", pin)
        self._set_status(f"Appairage de {name or mac}… l'imprimante peut demander une confirmation.")
        self._start(lambda progress, message: printer.pair_bluetooth(mac, name, pin=pin),
                    lambda result: self._paired(result, mac))

    def _paired(self, result, mac: str) -> None:
        if not result.ok:
            self._set_status(result.message, "#d93025")
            return
        self.mac = mac
        self._accepted = True
        self._set_status(result.message, "#188038")
        self._sync_buttons()
        QTimer.singleShot(900, self.accept)  # laisse le temps de lire la confirmation

    # ------------------------------------------------------------ fermeture
    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            self._set_status("Opération en cours : patientez avant de fermer.")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._set_status("Opération en cours : patientez avant de fermer.")
            event.ignore()
            return
        super().closeEvent(event)
