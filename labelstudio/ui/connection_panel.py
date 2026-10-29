"""Panneau « Connexion » : Bluetooth RFCOMM, USB (lp) ou série, avec test de liaison."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import printer


class ConnectionPanel(QWidget):
    testRequested = pyqtSignal()
    selftestRequested = pyqtSignal()
    detectRequested = pyqtSignal()
    pairRequested = pyqtSignal()
    forgetRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.qs = QSettings("PhomemoLabelStudio", "LabelStudio")
        root = QVBoxLayout(self)

        top = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Bluetooth (RFCOMM)", "bluetooth")
        self.kind.addItem("USB (/dev/usb/lp…)", "usb")
        self.kind.addItem("Port série (ttyUSB / rfcomm)", "serial")
        top.addRow("Mode de connexion", self.kind)
        root.addLayout(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # --- Bluetooth
        bt = QWidget()
        f = QFormLayout(bt)
        self.paired = QComboBox()
        self.refresh_btn = QPushButton("Actualiser")
        self.pair_btn = QPushButton("Appairer…")
        self.pair_btn.setToolTip("Rechercher et appairer une imprimante Bluetooth sans passer par le terminal")
        row = QHBoxLayout()
        row.addWidget(self.paired, 1)
        row.addWidget(self.refresh_btn)
        f.addRow("Appareils appairés", row)
        self.forget_btn = QPushButton("Oublier")
        self.forget_btn.setToolTip("Retirer l'appareil sélectionné des appareils appairés de BlueZ "
                                   "(il faudra l'appairer de nouveau pour l'utiliser)")
        bt_actions = QHBoxLayout()
        bt_actions.addWidget(self.pair_btn, 1)
        bt_actions.addWidget(self.forget_btn)
        f.addRow("", bt_actions)
        self.mac = QLineEdit()
        self.mac.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        self.mac.setInputMask(">HH:HH:HH:HH:HH:HH;_")
        f.addRow("Adresse MAC", self.mac)
        self.channel = QSpinBox()
        self.channel.setRange(1, 30)
        self.channel.setValue(1)
        f.addRow("Canal RFCOMM", self.channel)
        hint = QLabel("L'imprimante doit d'abord être appairée dans les paramètres Bluetooth de Linux Mint.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        f.addRow(hint)
        self.stack.addWidget(bt)

        # --- USB
        usb = QWidget()
        f = QFormLayout(usb)
        self.usb_path = QComboBox()
        self.usb_path.setEditable(True)
        f.addRow("Périphérique", self.usb_path)
        self.stack.addWidget(usb)

        # --- Série
        ser = QWidget()
        f = QFormLayout(ser)
        self.ser_path = QComboBox()
        self.ser_path.setEditable(True)
        f.addRow("Port", self.ser_path)
        self.baud = QComboBox()
        for b in (9600, 19200, 38400, 57600, 115200, 230400):
            self.baud.addItem(str(b), b)
        self.baud.setCurrentText("115200")
        f.addRow("Vitesse (bauds)", self.baud)
        self.stack.addWidget(ser)

        # --- actions
        self.detect_btn = QPushButton("🔍 Détecter l'imprimante")
        self.detect_btn.setToolTip("Cherche l'imprimante en Bluetooth (appairée), en USB ou en série, "
                                   "remplit la connexion et teste la liaison")
        self.test_btn = QPushButton("Tester la connexion")
        self.selftest_btn = QPushButton("Auto-test de l'imprimante (SELFTEST)")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.detect_btn)
        root.addWidget(self.test_btn)
        root.addWidget(self.selftest_btn)
        root.addWidget(self.status)
        root.addStretch(1)

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.paired.activated.connect(self._pick_paired)
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.detect_btn.clicked.connect(self.detectRequested)
        self.pair_btn.clicked.connect(self.pairRequested)
        self.forget_btn.clicked.connect(self.forgetRequested)
        self.test_btn.clicked.connect(self.testRequested)
        self.selftest_btn.clicked.connect(self.selftestRequested)
        for w in (self.mac, self.usb_path.lineEdit(), self.ser_path.lineEdit()):
            w.textChanged.connect(self.save)
        for w in (self.channel,):
            w.valueChanged.connect(self.save)
        self.baud.currentIndexChanged.connect(self.save)

        self._restore()
        self.refresh_devices()

    # ------------------------------------------------------------ état
    def set_busy(self, busy: bool) -> None:
        for b in (self.detect_btn, self.pair_btn, self.forget_btn, self.test_btn, self.selftest_btn):
            b.setEnabled(not busy)

    def set_status(self, text: str, ok: bool | None = None) -> None:
        color = {True: "#188038", False: "#d93025", None: "gray"}[ok]
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    def mac_address(self) -> str:
        """Adresse saisie, sans les séparateurs vides laissés par le masque de saisie."""
        text = self.mac.text().strip().upper().replace("_", "")
        return text if any(ch.isalnum() for ch in text) else ""

    def config(self) -> dict:
        kind = self.kind.currentData()
        if kind == "bluetooth":
            return {"type": "bluetooth", "mac": self.mac_address(), "channel": self.channel.value()}
        if kind == "usb":
            return {"type": "usb", "path": self.usb_path.currentText().strip()}
        return {"type": "serial", "path": self.ser_path.currentText().strip(), "baudrate": self.baud.currentData()}

    # ------------------------------------------------------------ interne
    def _kind_changed(self) -> None:
        self.stack.setCurrentIndex(self.kind.currentIndex())
        self.save()

    def refresh_devices(self) -> None:
        current = self.paired.currentData()
        self.paired.clear()
        devices = printer.list_paired_devices()
        for mac, name in devices:
            self.paired.addItem(f"{name}  ({mac})", mac)
        if not devices:
            self.paired.addItem("(aucun appareil appairé détecté)", None)
        # Pré-sélectionne un appareil dont le nom ressemble à la Phomemo
        for i in range(self.paired.count()):
            if "PM-241" in self.paired.itemText(i).upper() or "PHOMEMO" in self.paired.itemText(i).upper():
                self.paired.setCurrentIndex(i)
                if not self.mac_address():
                    self._pick_paired(i)
        if current:
            self.paired.setCurrentIndex(max(0, self.paired.findData(current)))

        for combo, paths, default in ((self.usb_path, printer.list_usb_devices(), "/dev/usb/lp0"),
                                       (self.ser_path, printer.list_serial_ports(), "/dev/ttyUSB0")):
            text = combo.currentText()
            combo.clear()
            combo.addItems(paths or [default])
            combo.setCurrentText(text or (paths[0] if paths else default))

    def _pick_paired(self, index: int) -> None:
        mac = self.paired.itemData(index)
        if mac:
            self.mac.setText(mac)

    def selected_paired(self) -> tuple[str, str]:
        """(MAC, nom) de l'appareil appairé choisi dans la liste, sinon de l'adresse saisie."""
        mac = (self.paired.currentData() or "").strip().upper()
        if mac:
            return mac, self.paired.itemText(self.paired.currentIndex()).rsplit("  (", 1)[0]
        return self.mac_address(), ""

    def remove_paired(self, mac: str) -> None:
        """Retire un appareil de la liste après un oubli réussi et vide la MAC s'il était choisi."""
        mac = (mac or "").strip().upper()
        position = self.paired.findData(mac)
        if position >= 0:
            self.paired.removeItem(position)
        if self.paired.count() == 0:
            self.paired.addItem("(aucun appareil appairé détecté)", None)
        if self.mac_address() == mac:
            self.mac.clear()
        self.save()

    def use_bluetooth(self, mac: str) -> None:
        """Bascule la connexion sur ce périphérique Bluetooth (après un appairage)."""
        self.kind.setCurrentIndex(max(0, self.kind.findData("bluetooth")))
        self.stack.setCurrentIndex(self.kind.currentIndex())
        self.refresh_devices()
        self.mac.setText(mac)
        position = self.paired.findData(mac)
        if position >= 0:
            self.paired.setCurrentIndex(position)
        self.save()

    def apply_detection(self, detection: printer.Detection) -> None:
        """Renseigne la connexion avec l'imprimante trouvée, sans toucher au reste de l'interface."""
        self.refresh_devices()
        chosen = detection.chosen
        if chosen is not None:
            index = self.kind.findData(chosen.kind)
            if index >= 0:
                self.kind.setCurrentIndex(index)
                self.stack.setCurrentIndex(index)
            if chosen.kind == "bluetooth":
                self.mac.setText(chosen.config["mac"])
                self.channel.setValue(int(chosen.config.get("channel", 1)))
                position = self.paired.findData(chosen.config["mac"])
                if position >= 0:
                    self.paired.setCurrentIndex(position)
            elif chosen.kind == "usb":
                self.usb_path.setCurrentText(chosen.config["path"])
            else:
                self.ser_path.setCurrentText(chosen.config["path"])
                self.baud.setCurrentIndex(max(0, self.baud.findData(int(chosen.config.get("baudrate", 115200)))))
            self.save()
        if chosen is None:
            self.set_status(detection.summary(), ok=False)
        elif detection.result is not None and detection.result.ok:
            self.set_status(detection.summary(), ok=True)
        else:
            self.set_status(detection.summary())

    def save(self, *_args) -> None:
        self.qs.setValue("conn/kind", self.kind.currentData())
        self.qs.setValue("conn/mac", self.mac.text())
        self.qs.setValue("conn/channel", self.channel.value())
        self.qs.setValue("conn/usb", self.usb_path.currentText())
        self.qs.setValue("conn/serial", self.ser_path.currentText())
        self.qs.setValue("conn/baud", self.baud.currentData())

    def _restore(self) -> None:
        kind = self.qs.value("conn/kind", "bluetooth")
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.stack.setCurrentIndex(self.kind.currentIndex())
        self.mac.setText(str(self.qs.value("conn/mac", "")))
        self.channel.setValue(int(self.qs.value("conn/channel", 1)))
        self.usb_path.setCurrentText(str(self.qs.value("conn/usb", "/dev/usb/lp0")))
        self.ser_path.setCurrentText(str(self.qs.value("conn/serial", "/dev/ttyUSB0")))
        self.baud.setCurrentIndex(max(0, self.baud.findData(int(self.qs.value("conn/baud", 115200)))))
