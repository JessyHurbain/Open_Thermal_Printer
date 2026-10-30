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

from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QIcon, QMouseEvent, QPixmap  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QInputDialog, QLabel, QMessageBox, QPushButton, QStyleOptionViewItem, QToolBar, QToolButton,
)

from open_thermal_printer import APP_NAME, diagnostics, printer, profiles, templates  # noqa: E402
from open_thermal_printer.settings import LEGACY, MIGRATED_KEY, migrate  # noqa: E402
from open_thermal_printer.settings import settings as app_settings  # noqa: E402
from open_thermal_printer.models import KIND_ICONS, Element, Label, LabelSettings  # noqa: E402
from open_thermal_printer.ui.element_list import (  # noqa: E402
    DETAIL_ROLE, ICON_ROLE, ICON_WIDTH, LEFT_MARGIN, ROW_HEIGHT, THUMB_SIZE, ElementItemDelegate,
)
from open_thermal_printer.ui.canvas import (  # noqa: E402
    GUIDE_COLOR, MARGIN, ORIGIN, RULER, LabelCanvas, alignment_offset, ruler_steps,
)
from open_thermal_printer.ui import error_report  # noqa: E402
from open_thermal_printer.ui.connection_panel import ConnectionPanel  # noqa: E402
from open_thermal_printer.ui.main_window import ADD_BUTTON_HEIGHT, GLYPH_SIZE, MainWindow  # noqa: E402
from open_thermal_printer.ui.pairing_dialog import PairingDialog  # noqa: E402

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
        for store in (app_settings(), QSettings(*LEGACY)):
            path = os.path.realpath(store.fileName())
            sandbox = os.path.realpath(_SETTINGS_DIR.name)
            self.assertTrue(path.startswith(sandbox + os.sep), path)


class SettingsMigrationTests(unittest.TestCase):
    """Le renommage ne doit pas faire disparaître les réglages de l'ancienne identité."""

    def setUp(self):
        self.legacy = QSettings(*LEGACY)
        self.current = app_settings()
        for store in (self.legacy, self.current):
            store.clear()
            store.sync()

    def test_legacy_values_are_carried_over_once(self):
        self.legacy.setValue("pair/pin", "1234")
        self.legacy.setValue("conn/mac", "AA:BB:CC:DD:EE:FF")
        self.assertTrue(migrate())
        fresh = app_settings()
        self.assertEqual(str(fresh.value("pair/pin")), "1234")
        self.assertEqual(str(fresh.value("conn/mac")), "AA:BB:CC:DD:EE:FF")
        self.assertTrue(fresh.value(MIGRATED_KEY, False, type=bool))
        self.assertFalse(migrate())  # marqueur posé : la reprise ne se rejoue plus

    def test_existing_values_are_never_overwritten(self):
        self.legacy.setValue("pair/pin", "1234")
        self.current.setValue("pair/pin", "9876")
        migrate()
        self.assertEqual(str(app_settings().value("pair/pin")), "9876")

    def test_fresh_install_is_left_alone(self):
        self.assertFalse(migrate())
        self.assertFalse(app_settings().contains(MIGRATED_KEY))


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


class PrinterProfileTests(unittest.TestCase):
    """Profils de périphériques : enregistrer, appliquer, supprimer depuis l'onglet Connexion."""

    def setUp(self):
        store = app_settings()
        store.clear()
        store.sync()
        for patch in patched_discovery(paired=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]):
            patch.start()
            self.addCleanup(patch.stop)
        self.panel = ConnectionPanel()
        self.addCleanup(self.panel.deleteLater)
        self.panel.kind.setCurrentIndex(self.panel.kind.findData("bluetooth"))
        self.panel.mac.setText("AA:BB:CC:DD:EE:FF")
        self.settings = {"width_mm": 50.0, "height_mm": 30.0, "gap_mm": 2.0,
                         "media": "gap", "dpi": 300, "density": 12, "speed": 3}
        self.panel.label_snapshot = lambda: dict(self.settings)

    def _save(self, name: str) -> None:
        with mock.patch.object(QInputDialog, "getText", return_value=(name, True)):
            self.panel.save_profile()

    def test_saving_records_connection_and_label_defaults(self):
        self._save("Prix 2 pouces")
        self.assertIn("Prix 2 pouces", self.panel.profiles)
        profile = self.panel.profiles["Prix 2 pouces"]
        self.assertEqual(profile.connection(), {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1})
        self.assertEqual(profile.label_defaults(), self.settings)
        # mémorisé dans QSettings, pas seulement à l'écran
        stored = profiles.load_profiles(app_settings().value(profiles.PROFILES_KEY, ""))
        self.assertEqual(stored["Prix 2 pouces"].label_defaults(), self.settings)
        self.assertEqual(self.panel.profile.currentText(), "Prix 2 pouces")

    def test_an_empty_name_saves_nothing(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("   ", True)):
            self.panel.save_profile()
        self.assertEqual(self.panel.profiles, {})

    def test_applying_switches_connection_and_announces_the_label_defaults(self):
        self._save("Prix")
        seen = []
        self.panel.profileApplied.connect(seen.append)
        # on s'éloigne du profil : autre imprimante, autres réglages
        self.panel.mac.setText("11:22:33:44:55:66")
        self.panel.channel.setValue(4)
        self.settings.update({"width_mm": 100.0, "height_mm": 150.0, "dpi": 203, "density": 6})
        self.panel.apply_profile()
        self.assertEqual(self.panel.config(), {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1})
        self.assertEqual(seen, [{"width_mm": 50.0, "height_mm": 30.0, "gap_mm": 2.0,
                                 "media": "gap", "dpi": 300, "density": 12, "speed": 3}])
        self.assertEqual(app_settings().value(profiles.LAST_KEY), "Prix")  # dernier profil utilisé

    def test_applying_never_touches_the_document_content(self):
        """Le profil ne transporte que les réglages d'impression, jamais des éléments d'étiquette."""
        self._save("Prix")
        self.assertNotIn("elements", self.panel.profiles["Prix"].label_defaults())
        self.assertEqual(set(self.panel.profiles["Prix"].label_defaults()), set(profiles.LABEL_FIELDS))

    def test_a_refused_overwrite_keeps_the_stored_profile(self):
        self._save("Prix")
        self.settings.update({"dpi": 203, "density": 4})
        with mock.patch.object(QInputDialog, "getText", return_value=("Prix", True)), \
             mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.panel.save_profile()
        self.assertEqual(self.panel.profiles["Prix"].dpi, 300)  # inchangé

    def test_an_accepted_overwrite_replaces_the_profile(self):
        self._save("Prix")
        self.settings.update({"dpi": 203, "density": 4})
        with mock.patch.object(QInputDialog, "getText", return_value=("Prix", True)), \
             mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.save_profile()
        self.assertEqual((self.panel.profiles["Prix"].dpi, self.panel.profiles["Prix"].density), (203, 4))

    def test_deleting_asks_first_and_leaves_the_connection_alone(self):
        self._save("Prix")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.panel.delete_profile()
        self.assertIn("Prix", self.panel.profiles)  # refus : rien ne bouge
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.delete_profile()
        self.assertEqual(self.panel.profiles, {})
        self.assertEqual(app_settings().value(profiles.PROFILES_KEY, ""), "{}")
        self.assertEqual(self.panel.config()["mac"], "AA:BB:CC:DD:EE:FF")  # connexion conservée
        self.assertFalse(self.panel.profile_apply.isEnabled())  # plus de profil à appliquer

    def test_profiles_are_relaunched_with_the_panel(self):
        self._save("Prix")
        again = ConnectionPanel()
        self.addCleanup(again.deleteLater)
        self.assertEqual(list(again.profiles), ["Prix"])
        self.assertEqual(again.profile.currentText(), "Prix")


class MainWindowProfileTests(unittest.TestCase):
    """Le profil appliqué change les réglages du document, jamais son contenu."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery(paired=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]):
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        store = app_settings()
        store.clear()
        store.sync()
        self.win = MainWindow()
        self.win._probe_timer.stop()
        pump(0.15)
        self.addCleanup(self.win.deleteLater)

    def test_applying_a_profile_rewrites_the_print_settings_but_not_the_label(self):
        panel, settings = self.win.conn_panel, self.win.label.settings
        panel.kind.setCurrentIndex(panel.kind.findData("bluetooth"))
        panel.mac.setText("AA:BB:CC:DD:EE:FF")
        settings.width_mm, settings.height_mm = 50.0, 30.0
        settings.dpi, settings.density, settings.speed = 300, 12, 3
        with mock.patch.object(QInputDialog, "getText", return_value=("Prix 2 pouces", True)):
            panel.save_profile()

        # l'utilisateur revient à une autre imprimante et à d'autres réglages
        settings.width_mm, settings.height_mm = 100.0, 150.0
        settings.dpi, settings.density, settings.speed = 203, 8, 5
        elements = len(self.win.label.elements)
        panel.apply_profile()

        self.assertEqual((settings.width_mm, settings.height_mm), (50.0, 30.0))
        self.assertEqual((settings.dpi, settings.density, settings.speed), (300, 12, 3))
        self.assertEqual(len(self.win.label.elements), elements)  # contenu préservé
        self.assertEqual(self.win.label_panel.dpi.currentData(), 300)  # panneau rechargé
        self.assertTrue(self.win.dirty)
        self.assertIn("Profil appliqué", self.win.log_view.toPlainText())


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
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
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
        app_settings().setValue("pair/pin", "0000")

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


class CanvasGridTests(unittest.TestCase):
    """Quadrillage de l'aperçu : alignement au pas choisi, sans toucher au rendu d'impression."""

    def setUp(self):
        self.canvas = LabelCanvas(templates.shipping_100x150())
        self.addCleanup(self.canvas.deleteLater)

    def test_snap_rounds_to_the_nearest_step(self):
        self.canvas.set_grid(5.0, True, True)
        self.assertEqual(self.canvas.snap_mm(12.4), 10.0)
        self.assertEqual(self.canvas.snap_mm(13.0), 15.0)

    def test_snap_off_keeps_the_tenth_of_a_millimetre(self):
        self.canvas.set_grid(5.0, True, False)
        self.assertEqual(self.canvas.snap_mm(12.34), 12.3)

    def test_step_is_used_as_given(self):
        self.canvas.set_grid(10.0, True, True)
        self.assertEqual(self.canvas.snap_mm(16.0), 20.0)

    def test_editing_state_does_not_change_the_label(self):
        self.canvas.set_grid(5.0, True, True)
        self.assertEqual(self.canvas.label.settings.width_mm, 100.0)
        self.assertEqual(len(self.canvas.label.elements), len(templates.shipping_100x150().elements))


class CanvasRulerTests(unittest.TestCase):
    """Règles graduées : place réservée autour de l'étiquette, pas adapté au zoom, dessin effectif."""

    def setUp(self):
        self.canvas = LabelCanvas(templates.shipping_100x150())
        self.addCleanup(self.canvas.deleteLater)

    def test_label_is_offset_by_the_ruler_band(self):
        rect = self.canvas._label_rect()
        self.assertEqual((rect.left(), rect.top()), (ORIGIN, ORIGIN))
        self.assertEqual(self.canvas.frame_px, ORIGIN + MARGIN)

    def test_widget_grows_to_fit_the_rulers(self):
        ppm = self.canvas.px_per_mm
        self.assertEqual(self.canvas.width(), int(100 * ppm) + ORIGIN + MARGIN)
        self.assertEqual(self.canvas.height(), int(150 * ppm) + ORIGIN + MARGIN)

    def test_steps_follow_the_zoom(self):
        self.assertEqual(ruler_steps(1.0), (50.0, 20.0))  # vue d'ensemble : repères tous les 5 cm
        self.assertEqual(ruler_steps(6.0), (5.0, 2.0))
        self.assertEqual(ruler_steps(20.0), (2.0, 1.0))  # fort zoom : au millimètre

    def test_rulers_are_painted_around_the_label(self):
        image = self.canvas.grab().toImage()
        self.assertEqual(image.pixelColor(ORIGIN + 50, ORIGIN - RULER + 1).name(), "#f1f3f4")
        self.assertEqual(image.pixelColor(ORIGIN - RULER + 1, ORIGIN + 50).name(), "#f1f3f4")
        # au moins une graduation tracée sur chaque règle (trait ou chiffre)
        top = {image.pixelColor(x, ORIGIN - RULER + 16).name() for x in range(ORIGIN, ORIGIN + 300)}
        left = {image.pixelColor(ORIGIN - RULER + 18, y).name() for y in range(ORIGIN, ORIGIN + 450)}
        self.assertIn("#5f6368", top)
        self.assertIn("#5f6368", left)


class CanvasGuideTests(unittest.TestCase):
    """Guides magnétiques : l'élément glissé se cale sur les bords et centres voisins."""

    def setUp(self):
        label = Label(
            settings=LabelSettings(width_mm=100, height_mm=50),
            elements=[
                Element(kind="rect", x=10.0, y=10.0, width=20.0, height=10.0),
                Element(kind="rect", x=70.0, y=30.0, width=20.0, height=10.0),
            ],
        )
        self.canvas = LabelCanvas(label)
        self.canvas.set_zoom(4.0)  # tolérance de 6 px = 1,5 mm
        self.addCleanup(self.canvas.deleteLater)

    def test_offset_takes_the_closest_target_within_tolerance(self):
        self.assertEqual(alignment_offset((10.0, 20.0), {11.0, 13.0}, 1.5), (1.0, 11.0))
        delta, target = alignment_offset((10.0,), {11.0, 10.4}, 1.5)
        self.assertAlmostEqual(delta, 0.4)
        self.assertEqual(target, 10.4)
        self.assertIsNone(alignment_offset((10.0,), {14.0}, 1.5))

    def test_dragged_element_snaps_to_a_neighbours_edge(self):
        x, y = self.canvas.magnetize(20.6, 31.0, 1)
        self.assertAlmostEqual(x, 20.0, delta=0.1)  # bord gauche ↔ bord droit du voisin
        self.assertEqual(y, 31.0)
        self.assertAlmostEqual(self.canvas._guides[0], x, delta=0.01)
        self.assertIsNone(self.canvas._guides[1])

    def test_dragged_element_snaps_to_the_label_centre(self):
        x, _y = self.canvas.magnetize(39.6, 44.0, 1)
        self.assertAlmostEqual(x, 40.0, delta=0.1)  # centre de l'élément ↔ centre de l'étiquette
        self.assertAlmostEqual(self.canvas._guides[0], 50.0)

    def test_far_away_nothing_is_snapped_and_guides_are_cleared(self):
        self.canvas.magnetize(20.6, 31.0, 1)
        x, y = self.canvas.magnetize(45.0, 30.0, 1)
        self.assertEqual((x, y), (45.0, 30.0))
        self.assertEqual(self.canvas._guides, (None, None))

    def test_suspended_guides_leave_the_position_free(self):
        x, y = self.canvas.magnetize(20.6, 31.0, 1, active=False)
        self.assertEqual((x, y), (20.6, 31.0))
        self.assertEqual(self.canvas._guides, (None, None))

    def test_alt_during_a_drag_suspends_the_snapping(self):
        self.canvas.setFocus()
        canvas = self.canvas
        start, target = self._at(80.0, 35.0), self._at(30.6, 35.0)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        self.addCleanup(lambda: QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=target))
        # Alt enfoncé : le glisser reste libre
        QApplication.sendEvent(canvas, self._move(target, Qt.KeyboardModifier.AltModifier))
        self.assertEqual(round(canvas.label.elements[1].x, 1), 20.5)  # 30,6 mm arrondi au pixel
        self.assertEqual(canvas._guides, (None, None))
        # sans Alt, le même geste s'accroche au bord du voisin
        QApplication.sendEvent(canvas, self._move(target, Qt.KeyboardModifier.NoModifier))
        self.assertAlmostEqual(canvas.label.elements[1].x, 20.0, delta=0.1)
        self.assertIsNotNone(canvas._guides[0])

    def _at(self, x_mm: float, y_mm: float) -> QPoint:
        k = self.canvas.px_per_mm
        return QPoint(int(ORIGIN + x_mm * k), int(ORIGIN + y_mm * k))

    def _move(self, pos: QPoint, modifiers: Qt.KeyboardModifier) -> QMouseEvent:
        point = QPointF(pos)
        return QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, modifiers)

    def test_active_guides_are_painted(self):
        self.canvas.magnetize(20.6, 31.0, 1)
        self.canvas.refresh()
        image = self.canvas.grab().toImage()
        column = int(ORIGIN + self.canvas._guides[0] * self.canvas.px_per_mm)
        colours = {image.pixelColor(x, y).name()
                   for x in range(column - 2, column + 3)
                   for y in range(ORIGIN, ORIGIN + 150)}
        self.assertIn(GUIDE_COLOR, colours)  # trait en pointillés : on balaie la colonne


class ElementListTests(unittest.TestCase):
    """Liste à deux lignes : titre, icône de type et détail, tenus à jour."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)

    def test_items_carry_a_title_an_icon_and_a_detail(self):
        item = self.win.elements_list.item(0)
        el = self.win.label.elements[0]
        self.assertEqual(item.text(), el.list_title())
        self.assertEqual(item.data(ICON_ROLE), KIND_ICONS[el.kind])  # secours du délégué
        self.assertEqual(item.data(DETAIL_ROLE), el.list_detail())
        self.assertFalse(item.icon().isNull())

    def test_thumbnails_show_the_rendered_element(self):
        for row in range(self.win.elements_list.count()):
            item = self.win.elements_list.item(row)
            image = item.icon().pixmap(THUMB_SIZE, THUMB_SIZE).toImage()
            dark = sum(1 for x in range(image.width()) for y in range(image.height())
                       if image.pixelColor(x, y).lightness() < 128)
            self.assertGreater(dark, 0, f"miniature vide : {item.text()}")

    def test_thumbnail_follows_the_element_settings(self):
        row = next(i for i, el in enumerate(self.win.label.elements) if el.kind == "barcode")
        self.win._select(row)
        item = self.win.elements_list.item(row)
        before = item.icon().pixmap(THUMB_SIZE, THUMB_SIZE).toImage()
        self.win.element_panel.bar_height.setValue(30.0)
        after = item.icon().pixmap(THUMB_SIZE, THUMB_SIZE).toImage()
        self.assertNotEqual(before, after)

    def test_thumbnail_stays_inside_its_frame(self):
        """Le style ne doit pas redessiner la miniature hors du cadre (icône qui dépasse à gauche)."""
        items = self.win.elements_list
        items.resize(320, 3 * ROW_HEIGHT)
        pixmap = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pixmap.fill(QColor("red"))  # couleur absente de l'interface : toute tache rouge vient d'elle
        items.item(0).setIcon(QIcon(pixmap))
        image = items.viewport().grab().toImage()
        frame_left = LEFT_MARGIN + (ICON_WIDTH - THUMB_SIZE) // 2  # même calcul que le délégué
        red = [x for x in range(image.width()) for y in range(ROW_HEIGHT)
               if image.pixelColor(x, y).name() == "#ff0000"]
        self.assertTrue(red, "la miniature n'a pas été peinte")
        self.assertGreaterEqual(min(red), frame_left)
        self.assertLessEqual(max(red), frame_left + THUMB_SIZE)

    def test_rows_are_tall_enough_for_two_lines(self):
        items = self.win.elements_list
        self.assertIsInstance(items.itemDelegate(), ElementItemDelegate)
        index = items.model().index(0, 0)
        self.assertEqual(items.itemDelegate().sizeHint(QStyleOptionViewItem(), index).height(), ROW_HEIGHT)

    def test_renaming_updates_the_first_line(self):
        self.win._select(0)
        self.win.element_panel.name.setText("Mon cadre")
        self.assertEqual(self.win.elements_list.item(0).text(), "Mon cadre")
        self.assertEqual(self.win.label.elements[0].name, "Mon cadre")


class AddButtonTests(unittest.TestCase):
    """Boutons d'ajout : icônes et légendes sur deux colonnes, bloc centré."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)
        # Affichée hors écran : les boutons n'ont leur géométrie réelle qu'une fois mis en page.
        self.win.resize(1440, 900)
        self.win.show()
        QApplication.processEvents()
        self.buttons = [b for b in self.win.elements_list.parentWidget().findChildren(QPushButton)
                        if b.findChild(QLabel, "kindGlyph") is not None]

    def test_glyphs_and_captions_form_two_columns(self):
        self.assertEqual(len(self.buttons), 5)
        glyphs = {b.findChild(QLabel, "kindGlyph").x() for b in self.buttons}
        captions = {b.findChild(QLabel, "kindCaption").x() for b in self.buttons}
        self.assertEqual(len(glyphs), 1, "les icônes ne sont pas sur une même colonne")
        self.assertEqual(len(captions), 1, "les légendes ne sont pas sur une même colonne")

    def test_the_block_is_centred_and_the_rows_have_one_height(self):
        self.assertEqual({b.height() for b in self.buttons}, {ADD_BUTTON_HEIGHT})
        for b in self.buttons:
            glyph = b.findChild(QLabel, "kindGlyph")
            caption = b.findChild(QLabel, "kindCaption")
            centre = (glyph.x() + caption.x() + caption.width()) / 2
            self.assertAlmostEqual(centre, b.width() / 2, delta=1.5,
                                   msg=f"bloc décentré : {b.accessibleName()}")

    def test_clicking_a_caption_adds_the_element(self):
        """Les libellés laissent passer les clics : c'est le bouton qui doit réagir."""
        button = self.buttons[0]
        caption = button.findChild(QLabel, "kindCaption")
        before = len(self.win.label.elements)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton,
                         pos=caption.geometry().center())
        self.assertEqual(len(self.win.label.elements), before + 1)
        self.assertEqual(self.win.label.elements[-1].kind, "text")


class ToolbarTests(unittest.TestCase):
    """Barre d'outils : boutons de même hauteur, chacun avec une icône de taille fixe."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)
        self.win.resize(1440, 900)
        self.win.show()  # sinon les boutons n'ont pas encore leur hauteur réelle
        QApplication.processEvents()
        toolbar = self.win.findChild(QToolBar)
        self.buttons = [b for b in toolbar.findChildren(QToolButton) if b.text()]

    def test_buttons_share_one_height_and_a_fixed_icon_size(self):
        self.assertEqual(len(self.buttons), 6)
        heights = {b.height() for b in self.buttons}
        self.assertEqual(len(heights), 1, f"boutons de hauteurs différentes : {sorted(heights)}")
        for b in self.buttons:
            self.assertEqual(b.text(), b.defaultAction().iconText())
            self.assertFalse(b.icon().isNull(), b.text())
            self.assertEqual(b.iconSize().width(), GLYPH_SIZE, b.text())


class ErrorReportTests(unittest.TestCase):
    """Erreur inattendue : message court à l'écran, trace complète dans le journal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = diagnostics.setup(os.path.join(self.tmp.name, "journal.log"))
        self.addCleanup(diagnostics.restore_hooks)
        self.addCleanup(diagnostics.reset)

    def test_an_uncaught_exception_opens_a_dialog_pointing_to_the_journal(self):
        diagnostics.install_hook(error_report.report)
        try:
            raise ValueError("liaison perdue")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        pump(0.1)
        self.assertTrue(error_report._open, "aucune boîte de dialogue n'a été ouverte")
        box = error_report._open[-1]
        self.addCleanup(box.close)
        self.assertIn("ValueError : liaison perdue", box.informativeText())
        self.assertIn(str(self.log), box.informativeText())
        self.assertIn("Traceback", self.log.read_text(encoding="utf-8"))

    def test_the_help_menu_offers_the_journal(self):
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        win = MainWindow()
        win._probe_timer.stop()
        self.addCleanup(win.deleteLater)
        texts = [a.text() for m in win.menuBar().actions() if m.menu()
                 for a in m.menu().actions()]
        self.assertIn("Ouvrir le journal d'erreurs", texts)


class GridMenuTests(unittest.TestCase):
    """Les deux entrées d'Affichage pilotent l'aperçu et sont mémorisées."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        # Réglages neutres avant la construction : chaque test part d'un état connu.
        self.qs = app_settings()
        for key in ("view/grid", "view/snap", "view/grid_mm"):
            self.qs.remove(key)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)

    def test_grid_action_shows_and_persists_the_grid(self):
        self.win.a_grid.trigger()
        self.assertTrue(self.win.canvas.grid_visible)
        self.assertEqual(str(self.qs.value("view/grid")).lower(), "true")
        self.win.a_grid.trigger()
        self.assertFalse(self.win.canvas.grid_visible)

    def test_snap_action_aligns_the_element_drags(self):
        self.win.a_snap.trigger()
        self.assertTrue(self.win.canvas.snap)
        self.assertEqual(self.win.canvas.snap_mm(7.9), 10.0)

    def test_state_is_restored_on_the_next_start(self):
        self.win.a_grid.trigger()
        self.win.set_grid_step(10.0)
        second = MainWindow()
        second._probe_timer.stop()
        self.addCleanup(second.deleteLater)
        self.assertTrue(second.canvas.grid_visible)
        self.assertEqual(second.canvas.grid_mm, 10.0)

    def test_window_title_uses_the_application_name(self):
        self.assertIn(APP_NAME, self.win.windowTitle())
        self.win._set_dirty(True)
        self.assertIn(APP_NAME, self.win.windowTitle())

    def test_grid_step_menu_changes_the_step(self):
        self.win.a_snap.trigger()
        for action in self.win._step_group.actions():
            if action.text() == "10 mm":
                action.trigger()
        self.assertEqual(self.win.canvas.grid_mm, 10.0)
        self.assertEqual(float(self.qs.value("view/grid_mm")), 10.0)
        self.assertEqual(self.win.canvas.snap_mm(16.0), 20.0)


class UndoTests(unittest.TestCase):
    """Annuler / rétablir : ajout, suppression, déplacement et réglage passent par la même pile."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)

    def _at(self, x_mm: float, y_mm: float) -> QPoint:
        k = self.win.canvas.px_per_mm
        return QPoint(int(ORIGIN + x_mm * k), int(ORIGIN + y_mm * k))

    def test_added_element_is_removed_then_put_back(self):
        count = len(self.win.label.elements)
        self.assertFalse(self.win.a_undo.isEnabled())
        self.win.add_element("rect")
        self.assertEqual(len(self.win.label.elements), count + 1)
        self.assertTrue(self.win.a_undo.isEnabled())
        self.win.undo()
        self.assertEqual(len(self.win.label.elements), count)
        self.assertTrue(self.win.a_redo.isEnabled())
        self.win.redo()
        self.assertEqual(len(self.win.label.elements), count + 1)

    def test_deleted_element_returns_with_its_geometry(self):
        self.win._select(0)
        before = self.win.label.elements[0]
        geometry = (before.kind, before.x, before.y, before.width, before.height)
        self.win.delete_element()
        self.assertNotEqual(len(self.win.label.elements), len(templates.shipping_100x150().elements))
        self.win.undo()
        back = self.win.label.elements[0]
        self.assertEqual((back.kind, back.x, back.y, back.width, back.height), geometry)

    def test_pending_setting_change_is_undone_after_being_recorded(self):
        self.win._select(0)
        self.win.element_panel.x.setValue(42.0)  # signal « changed » → enregistrement différé
        self.assertAlmostEqual(self.win.label.elements[0].x, 42.0)
        self.win.undo()
        self.assertNotAlmostEqual(self.win.label.elements[0].x, 42.0)
        self.win.redo()
        self.assertAlmostEqual(self.win.label.elements[0].x, 42.0)

    def test_dragged_element_returns_to_its_place(self):
        label = Label(LabelSettings(width_mm=100, height_mm=50), [
            Element(kind="rect", name="Boîte", x=20.0, y=20.0, width=10.0, height=10.0),
        ])
        self.win._load_label(label)
        self.assertFalse(self.win.a_undo.isEnabled())
        self.win._select(0)
        start, end = self._at(25.0, 25.0), self._at(35.0, 27.0)
        QTest.mousePress(self.win.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.win.canvas, end)
        QTest.mouseRelease(self.win.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertAlmostEqual(self.win.label.elements[0].x, 30.0, delta=0.2)
        self.win.undo()
        self.assertAlmostEqual(self.win.label.elements[0].x, 20.0, delta=0.1)
        self.win.redo()
        self.assertAlmostEqual(self.win.label.elements[0].x, 30.0, delta=0.2)

    def test_saving_clears_the_dirty_mark_and_undo_brings_it_back(self):
        with tempfile.NamedTemporaryFile(suffix=".label.json", delete=False) as fh:
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.win.path = path
        self.win.add_element("rect")
        self.assertTrue(self.win.dirty)
        self.win.save_file()
        self.assertFalse(self.win.dirty)
        self.win.undo()
        self.assertTrue(self.win.dirty)

    def test_opening_a_document_starts_a_fresh_history(self):
        self.win.add_element("rect")
        self.assertTrue(self.win.a_undo.isEnabled())
        self.win._load_label(templates.blank(100, 50))
        self.assertFalse(self.win.a_undo.isEnabled())
        self.assertFalse(self.win.a_redo.isEnabled())


class _SilentLink(printer.Connection):
    """Liaison factice : compte les ouvertures et fermetures, répond « prête »."""

    def __init__(self):
        self.open_count = self.closed = 0

    def open(self):
        self.open_count += 1

    def close(self):
        self.closed += 1

    def _write(self, chunk):
        pass

    def read(self, size=64, timeout=1.0):
        return bytes([0])

    def check_alive(self):
        return True


class ProbeSessionTests(unittest.TestCase):
    """La surveillance garde sa liaison : pas de reconnexion Bluetooth à chaque vérification."""

    def setUp(self):
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie."))
        probe.start()
        self.addCleanup(probe.stop)
        for patch in patched_discovery():
            patch.start()
            self.addCleanup(patch.stop)
        single = mock.patch("open_thermal_printer.ui.main_window.QTimer.singleShot")
        single.start()
        self.addCleanup(single.stop)
        self.win = MainWindow()
        self.win._probe_timer.stop()
        self.addCleanup(self.win.deleteLater)
        self.cfg = {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1}

    def test_the_same_connection_reuses_the_session(self):
        first = self.win._probe_session_for(self.cfg, "tspl")
        self.assertIs(self.win._probe_session_for(dict(self.cfg), "tspl"), first)

    def test_another_connection_replaces_the_session(self):
        first = self.win._probe_session_for(self.cfg, "tspl")
        usb = {"type": "usb", "path": "/dev/usb/lp0"}
        second = self.win._probe_session_for(usb, "tspl")
        self.assertIsNot(first, second)
        self.assertIs(self.win._probe_session, second)

    def test_checking_a_session_that_was_replaced_does_nothing(self):
        self.win._probe_session_for(self.cfg, "tspl")
        stray = printer.StatusProbe(self.cfg, factory=lambda _c, _t: _SilentLink())
        self.assertIsNone(self.win._check_session(stray))

    def test_checking_the_current_session_queries_the_printer(self):
        link = _SilentLink()
        session = printer.StatusProbe(self.cfg, factory=lambda _c, _t: link)
        self.win._probe_session = session
        result = self.win._check_session(session)
        self.assertTrue(result.ok)
        self.assertEqual(link.open_count, 1)

    def test_periodic_check_reuses_the_open_link(self):
        self.win.conn_panel.use_bluetooth("AA:BB:CC:DD:EE:FF")
        cfg = self.win.conn_panel.config()
        link = _SilentLink()
        self.win._probe_session = printer.StatusProbe(cfg, factory=lambda _c, _t: link)
        for _check in range(2):  # deux passages de la surveillance périodique
            self.win.check_printer()
            for _ in range(60):
                if self.win._probe is None:
                    break
                pump(0.05)
        self.assertEqual(link.open_count, 1)  # une seule connexion pour les deux vérifications
        self.assertIn("joignable", self.win.printer_state.text())

    def test_releasing_the_link_closes_it(self):
        link = _SilentLink()
        self.win._probe_session = printer.StatusProbe(self.cfg, factory=lambda _c, _t: link)
        self.win._check_session(self.win._probe_session)
        self.win._close_probe_session()
        self.assertEqual(link.closed, 1)
        self.assertIsNone(self.win._probe_session)


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
