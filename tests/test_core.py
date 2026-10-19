"""Tests du cœur (sans interface) : `python -m unittest discover -s tests -v`"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image

from labelstudio import printer, templates
from labelstudio.imaging import invert_bytes, pack_bitmap, to_bilevel
from labelstudio.models import Element, Label, LabelSettings
from labelstudio.renderer import printer_anchor, render_element, render_label, warnings_for
from labelstudio.tspl import build_tspl
from labelstudio.zpl import build_zpl


def one(el: Element, **settings) -> Label:
    return Label(LabelSettings(**settings), [el])


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

    def test_missing_device_message(self):
        res = printer.test_connection({"type": "usb", "path": "/dev/usb/lp_inexistant"})
        self.assertFalse(res.ok)
        self.assertIn("introuvable", res.message)

    def test_unknown_type(self):
        with self.assertRaises(printer.PrinterError):
            printer.make_connection({"type": "wifi"})


if __name__ == "__main__":
    unittest.main()
