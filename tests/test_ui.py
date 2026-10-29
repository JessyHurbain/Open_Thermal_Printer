"""Tests d'interface hors écran (Qt `offscreen`) : `python -m unittest discover -s tests`

Ils verrouillent le câblage des parcours de connexion : panneau (adresse MAC, sélection, oubli),
détection non destructive, oubli Bluetooth et code PIN d'appairage. Aucun matériel n'est requis :
les appels à bluetoothctl sont simulés et QSettings est redirigé vers un dossier temporaire pour
ne pas toucher à la configuration de l'utilisateur.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # avant toute création de QApplication
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from labelstudio import printer  # noqa: E402
from labelstudio.ui.connection_panel import ConnectionPanel  # noqa: E402
from labelstudio.ui.main_window import MainWindow  # noqa: E402
from labelstudio.ui.pairing_dialog import PairingDialog  # noqa: E402

_APP: QApplication | None = None
_SETTINGS_DIR: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global _APP, _SETTINGS_DIR
    _SETTINGS_DIR = tempfile.TemporaryDirectory()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    # Les deux formats : Qt résout le chemin via NativeFormat même en IniFormat, et sans cela
    # les tests écriraient dans la vraie configuration de l'utilisateur (~/.config).
    for fmt in (QSettings.Format.IniFormat, QSettings.Format.NativeFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, _SETTINGS_DIR.name)
    _APP = QApplication.instance() or QApplication([])


def tearDownModule() -> None:
    if _SETTINGS_DIR is not None:
        _SETTINGS_DIR.cleanup()


def pump(seconds: float = 0.4) -> None:
    """Traite les événements Qt pendant un instant (threads d'arrière-plan, signaux)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _APP.processEvents()
        time.sleep(0.01)


def patched_discovery(paired=None, usb=None, serial=None):
    """Neutralise la découverte matérielle réelle pendant un parcours d'interface."""
    return [
        mock.patch.object(printer, "list_paired_devices", return_value=paired or []),
        mock.patch.object(printer, "list_usb_devices", return_value=usb or []),
        mock.patch.object(printer, "list_serial_ports", return_value=serial or []),
    ]


class SettingsSandboxTests(unittest.TestCase):
    def test_settings_never_touch_the_real_configuration(self):
        """Garde-fou : une redirection ratée écrirait dans ~/.config de l'utilisateur."""
        path = os.path.realpath(QSettings("PhomemoLabelStudio", "LabelStudio").fileName())
        sandbox = os.path.realpath(_SETTINGS_DIR.name)
        self.assertTrue(path.startswith(sandbox + os.sep), path)


class ConnectionPanelTests(unittest.TestCase):
    def setUp(self):
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        self.panel = ConnectionPanel()
        self.panel.kind.setCurrentIndex(self.panel.kind.findData("bluetooth"))
        self.panel.mac.clear()

    def test_empty_masked_field_is_not_an_address(self):
        self.assertEqual(self.panel.mac_address(), "")
        self.assertEqual(self.panel.config()["mac"], "")

    def test_selected_paired_returns_mac_and_name(self):
        self.panel.mac.setText("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(printer, "list_paired_devices",
                               return_value=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]):
            self.panel.refresh_devices()
        self.panel.paired.setCurrentIndex(self.panel.paired.findData("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(self.panel.selected_paired(), ("AA:BB:CC:DD:EE:FF", "PM-241-BT"))

    def test_remove_paired_clears_the_list_and_the_address(self):
        self.panel.mac.setText("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(printer, "list_paired_devices",
                               return_value=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]):
            self.panel.refresh_devices()
        self.panel.remove_paired("AA:BB:CC:DD:EE:FF")
        self.assertIsNone(self.panel.paired.currentData())
        self.assertEqual(self.panel.mac_address(), "")

    def test_apply_detection_fills_each_mode(self):
        for config, expected in (
            ({"type": "usb", "path": "/dev/usb/lp0"}, {"type": "usb", "path": "/dev/usb/lp0"}),
            ({"type": "serial", "path": "/dev/ttyUSB0", "baudrate": 115200},
             {"type": "serial", "path": "/dev/ttyUSB0", "baudrate": 115200}),
            ({"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1},
             {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1}),
        ):
            with self.subTest(config["type"]):
                device = printer.Device(config["type"], "test", config, 70)
                detection = printer.Detection([device], device, printer.TestResult(True, "ok"))
                self.panel.apply_detection(detection)
                self.assertEqual(self.panel.config(), expected)


class MainWindowConnectionTests(unittest.TestCase):
    def setUp(self):
        # La sonde périodique est neutralisée : elle appellerait le vrai port série.
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("labelstudio.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        pump(0.15)
        self.panel = self.win.conn_panel
        self.panel.kind.setCurrentIndex(self.panel.kind.findData("bluetooth"))
        self.panel.mac.clear()
        self.addCleanup(self.win.deleteLater)

    def _wait_worker(self, seconds: float = 2.0) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pump(0.02)
            if self.win._worker is None:
                pump(0.05)
                return

    def _usb_detection(self) -> printer.Detection:
        device = printer.Device("usb", "USB /dev/usb/lp0", {"type": "usb", "path": "/dev/usb/lp0"}, 70)
        return printer.Detection([device], device, printer.TestResult(True, "Liaison établie."))

    def _pending_detection(self) -> printer.Detection:
        device = printer.Device("bluetooth",
                                "Bluetooth PM-241-BT (AA:BB:CC:DD:EE:FF) — non appairé, "
                                "imprimante probable",
                                {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1}, 0,
                                "PM-241-BT", pending=True)
        return printer.Detection([device])

    def _detect_pending(self, answer):
        asked = []
        QSettings("PhomemoLabelStudio", "LabelStudio").setValue("pair/pin", "0000")

        def question(_parent, title, *_args, **_kwargs):
            asked.append(title)
            return answer

        with mock.patch.object(printer, "detect_printer", return_value=self._pending_detection()), \
             mock.patch.object(QMessageBox, "question", side_effect=question), \
             mock.patch.object(printer, "list_paired_devices", return_value=[]), \
             mock.patch.object(printer, "pair_bluetooth",
                               return_value=printer.PairResult(True, "PM-241-BT est appairée.")) as pair, \
             mock.patch.object(printer, "test_connection",
                               return_value=printer.TestResult(True, "Liaison établie.")):
            self.win.detect_printer()
            self._wait_worker()
            pump(0.1)
        return asked, pair

    def test_detection_offers_to_pair_a_nearby_printer(self):
        asked, pair = self._detect_pending(QMessageBox.StandardButton.Yes)
        self.assertEqual(asked, ["Appairer l'imprimante ?"])
        self.assertEqual(pair.call_args, mock.call("AA:BB:CC:DD:EE:FF", "PM-241-BT", pin="0000"))
        self.assertEqual(self.panel.mac_address(), "AA:BB:CC:DD:EE:FF")

    def test_pairing_offer_can_be_refused(self):
        asked, pair = self._detect_pending(QMessageBox.StandardButton.No)
        self.assertEqual(asked, ["Appairer l'imprimante ?"])
        pair.assert_not_called()

    def _detect(self, answer=QMessageBox.StandardButton.No, state=True):
        asked = []

        def question(_parent, title, _text, *_args, **_kwargs):
            asked.append(title)
            return answer

        with mock.patch.object(printer, "detect_printer", return_value=self._usb_detection()), \
             mock.patch.object(QMessageBox, "question", side_effect=question):
            self.win.detect_printer()
            self._wait_worker()
        return asked

    def test_detection_applies_when_nothing_is_configured(self):
        self.win._printer_ok = None
        asked = self._detect()
        self.assertEqual(asked, [])
        self.assertEqual(self.panel.config(), {"type": "usb", "path": "/dev/usb/lp0"})

    def test_detection_asks_before_replacing_a_working_connection(self):
        self.panel.mac.setText("11:22:33:44:55:66")
        self.win._printer_ok = True
        asked = self._detect(answer=QMessageBox.StandardButton.No)
        self.assertEqual(asked, ["Remplacer la connexion ?"])
        self.assertEqual(self.panel.config(),
                         {"type": "bluetooth", "mac": "11:22:33:44:55:66", "channel": 1})

    def test_detection_replaces_after_acceptance(self):
        self.panel.mac.setText("11:22:33:44:55:66")
        self.win._printer_ok = True
        asked = self._detect(answer=QMessageBox.StandardButton.Yes)
        self.assertEqual(asked, ["Remplacer la connexion ?"])
        self.assertEqual(self.panel.config(), {"type": "usb", "path": "/dev/usb/lp0"})

    def test_detection_replaces_a_known_unreachable_printer_without_asking(self):
        self.panel.mac.setText("11:22:33:44:55:66")
        self.win._printer_ok = False
        asked = self._detect()
        self.assertEqual(asked, [])
        self.assertEqual(self.panel.config(), {"type": "usb", "path": "/dev/usb/lp0"})

    def test_forget_success_clears_the_connection(self):
        self.panel.mac.setText("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(printer, "list_paired_devices",
                               return_value=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]):
            self.panel.refresh_devices()
        forget = printer.ForgetResult(True, "PM-241-BT a été oublié.")
        with mock.patch.object(printer, "forget_bluetooth", return_value=forget) as called, \
             mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.win.forget_printer()
            self._wait_worker()
        called.assert_called_once_with("AA:BB:CC:DD:EE:FF", "PM-241-BT")
        self.assertEqual(self.panel.mac_address(), "")
        self.assertEqual(self.win.printer_state.text(), "○ Imprimante : non configurée")

    def test_forget_is_cancelled_by_a_refusal(self):
        self.panel.mac.setText("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(printer, "forget_bluetooth") as called, \
             mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.win.forget_printer()
            pump(0.1)
        called.assert_not_called()
        self.assertEqual(self.panel.mac_address(), "AA:BB:CC:DD:EE:FF")


class PairingDialogTests(unittest.TestCase):
    def test_entered_pin_is_given_to_the_pairing(self):
        devices = [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True)]
        with mock.patch.object(printer, "discover_bluetooth_devices", return_value=devices), \
             mock.patch.object(printer, "list_paired_devices", return_value=[]):
            dialog = PairingDialog()
            self.addCleanup(dialog.deleteLater)
            pump(0.3)
            dialog.list.setCurrentRow(0)
            dialog.pin.setText("1234")
            with mock.patch.object(printer, "pair_bluetooth",
                                   return_value=printer.PairResult(True, "PM-241-BT est appairée.")) as called:
                dialog.pair()
                pump(0.3)
        self.assertEqual(called.call_args, mock.call("AA:BB:CC:DD:EE:FF", "PM-241-BT", pin="1234"))


if __name__ == "__main__":
    unittest.main()
