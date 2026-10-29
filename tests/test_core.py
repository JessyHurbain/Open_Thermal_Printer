"""Tests du cœur (sans interface) : `python -m unittest discover -s tests -v`"""

import errno
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image

from labelstudio import printer, templates
from labelstudio.history import History
from labelstudio.imaging import invert_bytes, pack_bitmap, to_bilevel
from labelstudio.models import KIND_ICONS, KIND_LABELS, Element, Label, LabelSettings
from labelstudio.renderer import printer_anchor, render_element, render_label, warnings_for
from labelstudio.tspl import build_tspl
from labelstudio.zpl import build_zpl


def one(el: Element, **settings) -> Label:
    return Label(LabelSettings(**settings), [el])


class HistoryTests(unittest.TestCase):
    """Pile d'annulation : parcours des états, branches abandonnées et taille bornée."""

    def test_undo_and_redo_walk_the_states(self):
        history = History("a")
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertTrue(history.push("b"))
        self.assertTrue(history.push("c"))
        self.assertEqual(history.undo(), "b")
        self.assertEqual(history.undo(), "a")
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo(), "b")
        self.assertEqual(history.redo(), "c")
        self.assertIsNone(history.redo())

    def test_identical_state_is_not_stacked(self):
        history = History("a")
        self.assertFalse(history.push("a"))
        self.assertEqual(len(history), 1)
        self.assertFalse(history.can_undo)

    def test_new_change_drops_the_redo_branch(self):
        history = History("a")
        history.push("b")
        history.undo()
        history.push("c")
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo(), "a")
        self.assertEqual(history.redo(), "c")

    def test_limit_forgets_the_oldest_states(self):
        history = History("s0", limit=3)
        for i in range(1, 6):
            history.push(f"s{i}")
        self.assertEqual(len(history), 3)
        self.assertEqual(history.undo(), "s4")
        self.assertEqual(history.undo(), "s3")
        self.assertIsNone(history.undo())

    def test_reset_starts_a_fresh_history(self):
        history = History("a")
        history.push("b")
        history.reset("z")
        self.assertEqual(history.state, "z")
        self.assertEqual(len(history), 1)
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)


class ElementIconTests(unittest.TestCase):
    def test_every_kind_gets_an_icon_and_a_two_line_label(self):
        for kind in KIND_ICONS:
            el = Element(kind=kind)
            self.assertTrue(KIND_ICONS[kind])
            self.assertTrue(el.list_title())
            self.assertTrue(el.list_detail().startswith(KIND_LABELS[kind]))

    def test_title_prefers_the_name_then_the_content(self):
        self.assertEqual(Element(kind="rect", name="Cadre").list_title(), "Cadre")
        self.assertEqual(Element(kind="text", text="Bonjour").list_title(), "Bonjour")
        self.assertEqual(Element(kind="rect").list_title(), "Rectangle")

    def test_detail_recalls_the_setting_of_each_kind(self):
        self.assertIn("20 × 10 mm", Element(kind="rect").list_detail())
        self.assertIn("Code 128", Element(kind="barcode").list_detail())
        self.assertIn("corps", Element(kind="text").list_detail())

    def test_printer_facing_names_never_carry_an_icon(self):
        # Les commentaires TSPL / ZPL reprennent display_name() : pas d'émoji dans les commandes.
        for kind in KIND_ICONS:
            name = Element(kind=kind).display_name()
            self.assertFalse(any(icon in name for icon in KIND_ICONS.values()))


class ImagingTests(unittest.TestCase):
    def test_pack_bitmap_polarity_and_padding(self):
        img = Image.new("1", (10, 2), 1)  # blanc
        img.putpixel((0, 0), 0)  # un point noir en haut à gauche
        wb, h, data = pack_bitmap(img)
        self.assertEqual((wb, h), (2, 2))
        self.assertEqual(data[0], 0b01111111)  # TSPL : 0 = noir
        self.assertEqual(data[1], 0xFF)  # bits de remplissage = blanc
        self.assertEqual(invert_bytes(data)[0], 0b10000000)  # ZPL : 1 = noir

    def test_dither_modes_return_bilevel(self):
        grad = Image.linear_gradient("L").resize((64, 64))
        for mode in ("threshold", "floyd", "bayer"):
            out = to_bilevel(grad, mode, 128)
            self.assertEqual(out.mode, "1")
            self.assertEqual(out.size, (64, 64))
            colors = {v for _, v in out.convert("L").getcolors()}
            self.assertTrue(colors <= {0, 255})


class RenderTests(unittest.TestCase):
    def test_label_size_at_203dpi(self):
        s = LabelSettings(width_mm=100, height_mm=150, dpi=203)
        self.assertEqual((s.width_dots, s.height_dots), (799, 1199))

    def test_barcode_formats(self):
        for sym, data in (("code128", "ABC-123"), ("code39", "ABC123"), ("ean13", "590123412345"),
                          ("ean8", "9638507"), ("upca", "03600029145")):
            r = render_element(Element("barcode", symbology=sym, text=data), LabelSettings())
            self.assertIsNone(r.error, f"{sym}: {r.error}")
            self.assertGreater(r.img.width, 20)

    def test_invalid_barcode_is_placeholder_not_crash(self):
        r = render_element(Element("barcode", symbology="ean13", text="abc"), LabelSettings())
        self.assertIsNotNone(r.error)
        self.assertTrue(warnings_for([r]))

    def test_qr_scales_with_cell_size(self):
        a = render_element(Element("qrcode", text="x", qr_cell=3), LabelSettings())
        b = render_element(Element("qrcode", text="x", qr_cell=6), LabelSettings())
        self.assertEqual(b.img.width, a.img.width * 2)

    def test_rotation_swaps_bbox(self):
        el = Element("text", text="Bonjour", rotation=0)
        r0 = render_element(el, LabelSettings())
        el.rotation = 90
        r90 = render_element(el, LabelSettings())
        self.assertEqual((r90.img.width, r90.img.height), (r0.img.height, r0.img.width))

    def test_text_draws_black_pixels_with_accents(self):
        img, _ = render_label(one(Element("text", text="Éléphant à côté", x=2, y=2)))
        self.assertIsNotNone(img.point(lambda v: 255 - v).getbbox())

    def test_missing_image_flagged(self):
        r = render_element(Element("image", image_b64=""), LabelSettings())
        self.assertEqual(r.error, "Image manquante")


class TsplTests(unittest.TestCase):
    def test_header_and_footer(self):
        data = build_tspl(templates.shipping_100x150(), preview=True).decode("cp1252")
        lines = data.split("\r\n")
        self.assertEqual(lines[0], "SIZE 100 mm,150 mm")
        self.assertIn("GAP 3 mm,0 mm", lines)
        self.assertIn("CLS", lines)
        self.assertEqual(lines[-2], "PRINT 1,1")

    def test_raster_bitmap_length_is_consistent(self):
        data = build_tspl(templates.shipping_100x150())
        head = data.index(b"BITMAP ")
        header_end = data.index(b",0,", head) + 3
        wb, h = [int(v) for v in data[head:header_end].decode().split(",")[2:4]]
        payload = data[header_end : header_end + wb * h]
        self.assertEqual(len(payload), wb * h)
        self.assertEqual(data[header_end + wb * h :], b"\r\nPRINT 1,1\r\n")

    def test_media_modes(self):
        lab = templates.blank(50, 30)
        lab.settings.media = "blackmark"
        self.assertIn(b"BLINE 3 mm,0 mm", build_tspl(lab))
        lab.settings.media = "continuous"
        self.assertIn(b"GAP 0 mm,0 mm", build_tspl(lab))

    def test_copies(self):
        lab = templates.blank(50, 30)
        lab.settings.copies = 5
        self.assertTrue(build_tspl(lab).endswith(b"PRINT 1,5\r\n"))

    def test_native_commands(self):
        lab = Label(LabelSettings(mode="native"), [
            Element("text", text='Dis "oui" é', x=10, y=10),
            Element("barcode", text="ABC123", x=10, y=30),
            Element("qrcode", text="http://a.b", x=10, y=60),
            Element("rect", x=1, y=1, width=20, height=10),
        ])
        data = build_tspl(lab)
        text = data.decode("cp1252")
        self.assertIn("CODEPAGE 1252", text)
        self.assertIn('\\"oui\\"', text)  # guillemets échappés
        self.assertIn(b"\xe9", data)  # é encodé en CP1252
        self.assertIn('BARCODE ', text)
        self.assertIn('"128"', text)
        self.assertIn("QRCODE ", text)
        self.assertIn("BOX ", text)
        self.assertNotIn("BITMAP", text)

    def test_native_rotation_anchor(self):
        """90° horaire : le texte s'étend à gauche et sous le point d'ancrage."""
        el = Element("text", text="Bonjour", x=10, y=10, rotation=90)
        r = render_element(el, LabelSettings(mode="native"), native=True)
        ax, ay = printer_anchor(r)
        self.assertEqual((ax, ay), (r.x + r.img.width, r.y))
        el.rotation = 180
        r = render_element(el, LabelSettings(mode="native"), native=True)
        self.assertEqual(printer_anchor(r), (r.x + r.img.width, r.y + r.img.height))
        el.rotation = 270
        r = render_element(el, LabelSettings(mode="native"), native=True)
        self.assertEqual(printer_anchor(r), (r.x, r.y + r.img.height))

    def test_preview_hides_binary(self):
        data = build_tspl(templates.shipping_100x150(), preview=True)
        self.assertIn(b"octets>", data)
        self.assertLess(len(data), 600)


class ZplTests(unittest.TestCase):
    def test_raster_zpl(self):
        text = build_zpl(templates.shipping_100x150()).decode()
        self.assertTrue(text.startswith("~SD16\r\n^XA"))
        self.assertIn("^PW799", text)
        self.assertIn("^GFA,", text)
        self.assertTrue(text.rstrip().endswith("^XZ"))

    def test_gfa_counts(self):
        text = build_zpl(templates.shipping_100x150()).decode()
        part = text[text.index("^GFA,") + 5 :].split("^FS")[0]
        total, total2, row, hexdata = part.split(",")
        self.assertEqual(total, total2)
        self.assertEqual(len(hexdata), int(total) * 2)
        self.assertEqual(int(total) % int(row), 0)

    def test_native_zpl(self):
        lab = templates.shipping_100x150()
        lab.settings.mode = "native"
        text = build_zpl(lab).decode()
        for token in ("^A0N", "^BCN", "^BQN", "^GB"):
            self.assertIn(token, text)


class ModelTests(unittest.TestCase):
    def test_json_roundtrip(self):
        lab = templates.shipping_100x150()
        lab.settings.copies = 3
        again = Label.from_json(lab.to_json())
        self.assertEqual(again.settings, lab.settings)
        self.assertEqual(again.elements, lab.elements)

    def test_unknown_fields_ignored(self):
        lab = Label.from_json('{"settings": {"width_mm": 40, "inconnu": 1}, "elements": [{"kind": "text", "zzz": 2}]}')
        self.assertEqual(lab.settings.width_mm, 40)
        self.assertEqual(lab.elements[0].kind, "text")


class PrinterTests(unittest.TestCase):
    def test_bad_mac_rejected(self):
        with self.assertRaises(printer.PrinterError):
            printer.make_connection({"type": "bluetooth", "mac": "pas-une-mac"})

    def test_usb_lp_writes_all_bytes(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            payload = bytes(range(256)) * 10  # 2560 octets, plusieurs paquets
            seen = []
            printer.send_job({"type": "usb", "path": path}, payload, seen.append)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), payload)
            self.assertEqual(seen[-1], 100)
        finally:
            os.unlink(path)

    def test_probe_timeout_is_short_for_bluetooth(self):
        conn = printer.make_connection({"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF"}, timeout=3)
        self.assertEqual(conn.timeout, 3)
        self.assertEqual(printer.make_connection({"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF"}).timeout, 10.0)

    def test_missing_device_message(self):
        res = printer.test_connection({"type": "usb", "path": "/dev/usb/lp_inexistant"})
        self.assertFalse(res.ok)
        self.assertIn("introuvable", res.message)

    def test_unknown_type(self):
        with self.assertRaises(printer.PrinterError):
            printer.make_connection({"type": "wifi"})


class DetectTests(unittest.TestCase):
    def test_phomemo_paired_device_comes_first(self):
        devices = printer.build_devices(
            [("11:22:33:44:55:66", "Souris"), ("AA:BB:CC:DD:EE:FF", "PM-241-BT")], [], [])
        self.assertEqual(devices[0].kind, "bluetooth")
        self.assertEqual(devices[0].config["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertTrue(devices[0].usable)
        self.assertEqual(devices[-1].config["mac"], "11:22:33:44:55:66")

    def test_usb_comes_before_serial_and_rfcomm_comes_first(self):
        devices = printer.build_devices([], ["/dev/usb/lp0"], ["/dev/ttyUSB0", "/dev/rfcomm0"])
        self.assertEqual([d.kind for d in devices], ["usb", "serial", "serial"])
        self.assertEqual(devices[1].config["path"], "/dev/rfcomm0")  # pont Bluetooth déjà ouvert
        self.assertEqual(devices[2].config["path"], "/dev/ttyUSB0")

    def test_generic_paired_device_is_not_taken_for_a_printer(self):
        devices = printer.build_devices([("94:DB:56:AA:87:92", "WH-CH700N")], [], [])
        self.assertFalse(devices[0].usable)
        self.assertIn("pas une imprimante", devices[0].label)
        self.assertIn("Aucune imprimante reconnue", printer.Detection(devices).summary())

    def test_generic_printer_name_is_accepted(self):
        devices = printer.build_devices([("AA:BB:CC:DD:EE:FF", "Thermal Printer 58")], [], [])
        self.assertTrue(devices[0].usable)
        self.assertNotIn("pas une imprimante", devices[0].label)

    def test_discovered_but_unpaired_device_is_reported_not_chosen(self):
        devices = printer.build_devices([], [], [], [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True)])
        self.assertEqual(len(devices), 1)
        self.assertFalse(devices[0].usable)
        self.assertIn("non appairé", devices[0].label)
        self.assertIn("imprimante probable", devices[0].label)
        self.assertIn("Appairez-la", printer.Detection(devices).summary())

    def test_bluetoothctl_output_is_parsed(self):
        out = "[NEW] Device AA:BB:CC:DD:EE:FF PM-241-BT\nDevice 11:22:33:44:55:66 Souris\n"
        self.assertEqual(printer._parse_devices(out),
                         [("AA:BB:CC:DD:EE:FF", "PM-241-BT"), ("11:22:33:44:55:66", "Souris")])

    def test_colored_scan_output_is_parsed(self):
        """bluetoothctl colore ses lignes quand la sortie est un tube : le scan doit rester lisible."""
        colored = ("[\x1b[0;92mNEW\x1b[0m] Device AA:BB:CC:DD:EE:FF PM-241-BT\n"
                   "[\x1b[0;93mCHG\x1b[0m] Device AA:BB:CC:DD:EE:FF Class: 0x00140680 (1312384)\n"
                   "[\x1b[0;93mCHG\x1b[0m] Device AA:BB:CC:DD:EE:FF Icon: printer\n")
        self.assertEqual(printer._parse_devices(colored)[0], ("AA:BB:CC:DD:EE:FF", "PM-241-BT"))
        self.assertEqual(printer._parse_nearby(colored),
                         [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True)])

    def test_property_lines_never_become_a_device_name(self):
        out = ("[CHG] Device AA:BB:CC:DD:EE:FF RSSI: 0xffffffc4 (-60)\n"
               "[CHG] Device AA:BB:CC:DD:EE:FF Name: PM-241-BT\n")
        self.assertEqual(printer._parse_nearby(out), [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT")])

    def test_imaging_class_or_icon_marks_a_printer_without_the_name(self):
        printer_class = "[NEW] Device 11:22:33:44:55:66 11-22-33-44-55-66\n" \
                        "[CHG] Device 11:22:33:44:55:66 Class: 0x00140680\n"
        audio_class = "[NEW] Device 94:DB:56:AA:87:92 WH-CH700N\n" \
                      "[CHG] Device 94:DB:56:AA:87:92 Class: 0x00240404\n"
        self.assertTrue(printer._parse_nearby(printer_class)[0].printer)
        self.assertFalse(printer._parse_nearby(audio_class)[0].printer)

    def test_discovered_printer_is_marked_as_waiting_for_pairing(self):
        devices = printer.build_devices([], [], [],
                                        [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True)])
        self.assertTrue(devices[0].pending)
        self.assertEqual(devices[0].name, "PM-241-BT")
        paired = printer.build_devices([("94:DB:56:AA:87:92", "WH-CH700N")], [], [])
        self.assertFalse(paired[0].pending)

    def test_announced_printer_class_makes_a_paired_device_usable(self):
        devices = printer.build_devices([("AA:BB:CC:DD:EE:FF", "PM-241-BT")], [], [],
                                        printer_macs={"AA:BB:CC:DD:EE:FF"})
        self.assertTrue(devices[0].usable)
        self.assertNotIn("pas une imprimante", devices[0].label)

    def test_scan_reads_the_class_of_paired_devices(self):
        info = ("Device AA:BB:CC:DD:EE:FF (public)\n\tName: PM-241-BT\n"
                "\tClass: 0x00140680 (1312384)\n\tIcon: printer\n")
        with mock.patch.object(printer, "list_paired_devices",
                               return_value=[("AA:BB:CC:DD:EE:FF", "PM-241-BT")]), \
             mock.patch.object(printer, "list_usb_devices", return_value=[]), \
             mock.patch.object(printer, "list_serial_ports", return_value=[]), \
             mock.patch.object(printer, "_device_info", return_value=info):
            devices = printer.scan_devices()
        self.assertEqual(devices[0].config["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertTrue(devices[0].usable)

    def test_scan_also_lists_devices_bluez_already_knows(self):
        def fake_run(cmd, **_kwargs):
            if "scan" in cmd:
                return types.SimpleNamespace(stdout="[NEW] Device 11:22:33:44:55:66 11-22-33-44-55-66\n",
                                             stderr="")
            return types.SimpleNamespace(stdout="Device AA:BB:CC:DD:EE:FF PM-241-BT\n", stderr="")
        with mock.patch.object(printer.subprocess, "run", side_effect=fake_run):
            found = printer.discover_bluetooth_devices(timeout=1)
        self.assertEqual([d.mac for d in found], ["11:22:33:44:55:66", "AA:BB:CC:DD:EE:FF"])

    def test_scan_explains_an_off_or_missing_adapter(self):
        off = types.SimpleNamespace(stdout="Controller 00:11 (public)\n\tPowered: no\n", stderr="")
        missing = types.SimpleNamespace(stdout="No default controller available\n", stderr="")
        with mock.patch.object(printer.subprocess, "run", return_value=off):
            self.assertIn("éteint", printer.bluetooth_problem())
        with mock.patch.object(printer.subprocess, "run", return_value=missing):
            self.assertIn("Aucun adaptateur", printer.bluetooth_problem())
        fine = types.SimpleNamespace(stdout="Controller 00:11 (public)\n\tPowered: yes\n", stderr="")
        with mock.patch.object(printer.subprocess, "run", return_value=fine):
            self.assertEqual(printer.bluetooth_problem(), "")

    def test_scan_only_discovers_when_nothing_usable_is_known(self):
        with mock.patch.object(printer, "list_paired_devices", return_value=[]), \
             mock.patch.object(printer, "list_usb_devices", return_value=[]), \
             mock.patch.object(printer, "list_serial_ports", return_value=[]), \
             mock.patch.object(printer, "discover_bluetooth_devices",
                               return_value=[printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True)]) as scan:
            devices = printer.scan_devices()
        scan.assert_called_once()
        self.assertEqual(devices[0].config["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertFalse(devices[0].usable)  # à appairer d'abord

        with mock.patch.object(printer, "list_paired_devices", return_value=[]), \
             mock.patch.object(printer, "list_usb_devices", return_value=["/dev/usb/lp0"]), \
             mock.patch.object(printer, "list_serial_ports", return_value=[]), \
             mock.patch.object(printer, "discover_bluetooth_devices") as scan:
            printer.scan_devices()
        scan.assert_not_called()  # une imprimante USB suffit : pas de scan Bluetooth

    def test_detection_points_to_pairing_when_a_printer_is_nearby(self):
        devices = printer.build_devices(
            [("94:DB:56:AA:87:92", "WH-CH700N")], [], [],
            [printer.Nearby("AA:BB:CC:DD:EE:FF", "PM-241-BT", True),
             printer.Nearby("12:47:D2:85:35:A0", "12-47-D2-85-35-A0")])
        summary = printer.Detection(devices).summary()
        self.assertIn("pas encore appairée", summary)
        self.assertIn("PM-241-BT", summary)
        self.assertIn("Appairer", summary)
        self.assertNotIn("12-47-D2", summary)  # appareil quelconque : pas dans le message

    def test_detection_without_printer_says_so(self):
        with mock.patch.object(printer, "scan_devices", return_value=[]):
            detection = printer.detect_printer(discover=False)
        self.assertIsNone(detection.chosen)
        self.assertIsNone(detection.result)
        self.assertIn("Aucune imprimante détectée", detection.summary())

    def test_detection_probes_the_best_device(self):
        device = printer.Device("usb", "USB /dev/usb/lp0", {"type": "usb", "path": "/dev/usb/lp0"}, 70)
        probe = mock.patch.object(printer, "test_connection",
                                  return_value=printer.TestResult(True, "Liaison établie.\nModèle : PM-241"))
        with mock.patch.object(printer, "scan_devices", return_value=[device]), probe as called:
            detection = printer.detect_printer(discover=False)
        called.assert_called_once_with({"type": "usb", "path": "/dev/usb/lp0"}, "tspl")
        self.assertIs(detection.chosen, device)
        self.assertIn("Imprimante prête", detection.summary())


class BluetoothConnectTests(unittest.TestCase):
    @staticmethod
    def _socket(attempts, failure):
        class FakeSock:
            def settimeout(self, _timeout):
                pass

            def connect(self, _address):
                attempts.append(1)
                if failure(attempts):
                    raise OSError(errno.EBUSY, "Device or resource busy")

            def close(self):
                pass

        return lambda *_a, **_k: FakeSock()

    def test_busy_link_is_retried_once(self):
        """BlueZ garde parfois le lien quelques instants : la reconnexion immédiate ne doit pas échouer."""
        attempts: list[int] = []
        with mock.patch.object(printer.socket, "socket", self._socket(attempts, lambda a: len(a) == 1)), \
             mock.patch.object(printer.time, "sleep"):
            conn = printer.BluetoothConnection("AA:BB:CC:DD:EE:FF")
            conn.open()
            conn.close()
        self.assertEqual(len(attempts), 2)

    def test_persistent_busy_is_explained(self):
        attempts: list[int] = []
        with mock.patch.object(printer.socket, "socket", self._socket(attempts, lambda _a: True)), \
             mock.patch.object(printer.time, "sleep"):
            with self.assertRaises(printer.PrinterError) as caught:
                printer.BluetoothConnection("AA:BB:CC:DD:EE:FF").open()
        self.assertIn("déjà utilisée", str(caught.exception))


class PairTests(unittest.TestCase):
    @staticmethod
    def _done(stdout: str = "", stderr: str = "", code: int = 0):
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)

    def test_forgotten_device_is_scanned_again_before_pairing(self):
        """BlueZ oublie un appareil non appairé à la fin du scan : il faut le retrouver avant `pair`."""
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd[:3] == ["bluetoothctl", "devices", ] or cmd[1:] == ["devices"]:
                return self._done("Device 94:DB:56:AA:87:92 WH-CH700N\n")
            if "scan" in cmd:
                return self._done("[NEW] Device AA:BB:CC:DD:EE:FF PM-241-BT\n")
            if "trust" in cmd:
                return self._done("trust succeeded\n")
            return self._done("Pairing successful\n")

        with mock.patch.object(printer.subprocess, "run", side_effect=fake_run):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", "PM-241-BT", pin="0000")
        self.assertTrue(result.ok)
        self.assertTrue(any("scan" in cmd for cmd in calls))

    def test_a_known_device_is_paired_without_scanning(self):
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(cmd)
            if cmd[1:] == ["devices"]:
                return self._done("Device AA:BB:CC:DD:EE:FF PM-241-BT\n")
            if "trust" in cmd:
                return self._done("trust succeeded\n")
            return self._done("Pairing successful\n")

        with mock.patch.object(printer.subprocess, "run", side_effect=fake_run):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", "PM-241-BT", pin="0000")
        self.assertTrue(result.ok)
        self.assertFalse(any("scan" in cmd for cmd in calls))

    def test_pair_then_trust_succeeds(self):
        known = self._done("Device AA:BB:CC:DD:EE:FF PM-241-BT\n")
        outputs = [known, self._done("Pairing successful\n"), self._done("trust succeeded\n")]
        with mock.patch.object(printer.subprocess, "run", side_effect=outputs) as run:
            result = printer.pair_bluetooth("aa:bb:cc:dd:ee:ff", "PM-241-BT")
        self.assertTrue(result.ok)
        self.assertIn("PM-241-BT", result.message)
        self.assertIn("approuvée", result.message)
        self.assertEqual(run.call_args_list[1].args[0],
                         ["bluetoothctl", "--agent", "NoInputNoOutput", "pair", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(run.call_args_list[2].args[0][-2:], ["trust", "AA:BB:CC:DD:EE:FF"])

    def test_already_paired_counts_as_success(self):
        outputs = [self._done("Device AA:BB:CC:DD:EE:FF PM-241-BT\n"),
                   self._done("Failed to pair: org.bluez.Error.AlreadyExists\n"),
                   self._done("trust succeeded\n")]
        with mock.patch.object(printer.subprocess, "run", side_effect=outputs):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF")
        self.assertTrue(result.ok)

    def test_authentication_failure_explains_the_pin(self):
        with mock.patch.object(printer.subprocess, "run",
                               return_value=self._done("Failed to pair: org.bluez.Error.AuthenticationFailed\n")):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", "PM-241-BT")
        self.assertFalse(result.ok)
        self.assertIn("PIN", result.message)
        self.assertIn("0000", result.message)

    def test_vanished_device_is_reported(self):
        with mock.patch.object(printer.subprocess, "run",
                               return_value=self._done("", "Failed to pair: org.bluez.Error.DoesNotExist\n")):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", "PM-241-BT")
        self.assertFalse(result.ok)
        self.assertIn("n'est plus visible", result.message)

    def test_invalid_mac_is_rejected_without_calling_bluetoothctl(self):
        with mock.patch.object(printer.subprocess, "run") as run:
            result = printer.pair_bluetooth("pas-une-mac")
        self.assertFalse(result.ok)
        run.assert_not_called()

    def test_timeout_and_missing_tool_are_explained(self):
        with mock.patch.object(printer.subprocess, "run", side_effect=subprocess.TimeoutExpired("bluetoothctl", 30)):
            self.assertIn("Délai dépassé", printer.pair_bluetooth("AA:BB:CC:DD:EE:FF").message)
        with mock.patch.object(printer.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIn("bluetoothctl", printer.pair_bluetooth("AA:BB:CC:DD:EE:FF").message)

    def test_trust_failure_still_keeps_the_pairing(self):
        outputs = [self._done("Device AA:BB:CC:DD:EE:FF PM-241-BT\n"),
                   self._done("Pairing successful\n"), self._done("", "Failed to trust\n")]
        with mock.patch.object(printer.subprocess, "run", side_effect=outputs):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF")
        self.assertTrue(result.ok)
        self.assertIn("trust", result.message)

    def test_printer_names_are_recognised(self):
        self.assertTrue(printer.looks_like_printer("PM-241-BT"))
        self.assertTrue(printer.looks_like_printer("Thermal Printer"))
        self.assertFalse(printer.looks_like_printer("WH-CH700N"))

    def test_custom_pin_uses_the_interactive_agent(self):
        with mock.patch.object(printer, "_ensure_known"), \
             mock.patch.object(printer, "_pair_with_pin", return_value="Pairing successful\n") as typed, \
             mock.patch.object(printer, "_bluetoothctl", return_value=self._done("trust succeeded\n")):
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", "PM-241-BT", pin="1234")
        self.assertTrue(result.ok)
        typed.assert_called_once_with("AA:BB:CC:DD:EE:FF", "1234", 30.0)

    def test_pin_is_typed_when_the_agent_asks(self):
        class Sink:
            def __init__(self):
                self.data = []

            def write(self, text):
                self.data.append(text)

            def flush(self):
                pass

        class Out:
            def __init__(self, lines):
                self.lines = list(lines)

            def readline(self):
                return self.lines.pop(0) if self.lines else ""

        class Proc:
            def __init__(self):
                self.stdin = Sink()
                self.stdout = Out(["[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -40\n",
                                   "[agent] Enter PIN code: ",
                                   "Pairing successful\n"])
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        proc = Proc()
        with mock.patch.object(printer.subprocess, "Popen", return_value=proc), \
             mock.patch.object(printer.select, "select", return_value=([proc.stdout], [], [])):
            output = printer._pair_with_pin("AA:BB:CC:DD:EE:FF", "1234", 5)
        self.assertIn("pair AA:BB:CC:DD:EE:FF\n", proc.stdin.data)
        self.assertIn("1234\n", proc.stdin.data)
        self.assertIn("Pairing successful", output)

    def test_invalid_pin_is_rejected_without_pairing(self):
        with mock.patch.object(printer, "_pair_with_pin") as typed, \
             mock.patch.object(printer.subprocess, "run") as run:
            result = printer.pair_bluetooth("AA:BB:CC:DD:EE:FF", pin="12")
        self.assertFalse(result.ok)
        self.assertIn("4 à 6 chiffres", result.message)
        typed.assert_not_called()
        run.assert_not_called()


class ForgetTests(unittest.TestCase):
    @staticmethod
    def _done(stdout: str = "", stderr: str = "", code: int = 0):
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)

    def test_forget_removes_the_device(self):
        with mock.patch.object(printer.subprocess, "run",
                               return_value=self._done("Device AA:BB:CC:DD:EE:FF removed successfully\n")) as run:
            result = printer.forget_bluetooth("aa:bb:cc:dd:ee:ff", "PM-241-BT")
        self.assertTrue(result.ok)
        self.assertIn("PM-241-BT", result.message)
        self.assertEqual(run.call_args.args[0],
                         ["bluetoothctl", "--agent", "NoInputNoOutput", "remove", "AA:BB:CC:DD:EE:FF"])

    def test_already_forgotten_device_is_not_an_error(self):
        with mock.patch.object(printer.subprocess, "run",
                               return_value=self._done("", "Failed to remove device: org.bluez.Error.DoesNotExist\n")):
            result = printer.forget_bluetooth("AA:BB:CC:DD:EE:FF")
        self.assertTrue(result.ok)
        self.assertIn("n'était plus appairé", result.message)

    def test_invalid_mac_is_rejected_without_calling_bluetoothctl(self):
        with mock.patch.object(printer.subprocess, "run") as run:
            result = printer.forget_bluetooth("pas-une-mac")
        self.assertFalse(result.ok)
        run.assert_not_called()

    def test_switch_off_adapter_is_explained(self):
        with mock.patch.object(printer.subprocess, "run",
                               return_value=self._done("", "No default controller available\n")):
            result = printer.forget_bluetooth("AA:BB:CC:DD:EE:FF")
        self.assertFalse(result.ok)
        self.assertIn("Adaptateur Bluetooth", result.message)

    def test_timeout_and_missing_tool_are_explained(self):
        with mock.patch.object(printer.subprocess, "run", side_effect=subprocess.TimeoutExpired("bluetoothctl", 15)):
            self.assertIn("Délai dépassé", printer.forget_bluetooth("AA:BB:CC:DD:EE:FF").message)
        with mock.patch.object(printer.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIn("bluez", printer.forget_bluetooth("AA:BB:CC:DD:EE:FF").message)


if __name__ == "__main__":
    unittest.main()
