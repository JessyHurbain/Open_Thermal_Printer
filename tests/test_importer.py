"""Tests de l'import d'étiquettes de transport : `python -m unittest discover -s tests -v`"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw

from open_thermal_printer import importer


def page_with_label(box=(300, 200, 900, 1000), size=(1240, 1754)) -> Image.Image:
    """Fausse page A4 : un cadre d'étiquette + deux lignes d'instructions plus bas."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline="black", width=4)
    d.rectangle((box[0] + 40, box[1] + 40, box[2] - 40, box[1] + 160), fill="black")  # faux code-barres
    d.rectangle((100, 1500, 1100, 1530), fill="black")  # ligne de texte isolée (trop fine pour être un bloc)
    return img


def two_panel_page(size=(1700, 1240)) -> Image.Image:
    """Feuille paysage type Colissimo : bordereau encadré à gauche, notice encadrée à droite,
    et une légende verticale imprimée dans la gouttière (qui soude les deux panneaux)."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 800, 1200), outline="black", width=3)  # panneau du bordereau
    d.rectangle((80, 160, 760, 900), outline="black", width=2)  # cadre de l'étiquette
    d.rectangle((120, 620, 720, 800), fill="black")  # code-barres 1D
    d.rectangle((840, 40, 1660, 1200), outline="black", width=3)  # panneau de la notice (un peu plus grand)
    d.rectangle((880, 120, 1620, 190), fill=(120, 120, 120))  # bandeau de la notice
    for y in range(200, 1000, 14):  # légende verticale dans la gouttière
        d.line((818, y, 818, y + 7), fill="black", width=3)
    return img


class PdfPage:
    """Page d'un PDF fabriqué par les tests — coordonnées en points, origine en haut à gauche."""

    def __init__(self, width: float = 595, height: float = 842, rotation: int = 0):
        self.width, self.height, self.rotation = width, height, rotation
        self.ops: list[str] = []

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill: bool = True, thickness: float = 1.0) -> None:
        """Rectangle plein, ou cadre si `fill` est faux."""
        y = self.height - y1  # PDF : origine en bas à gauche
        if fill:
            self.ops.append(f"0 0 0 rg {x0} {y} {x1 - x0} {y1 - y0} re f")
        else:
            self.ops.append(f"0 G {thickness} w {x0} {y} {x1 - x0} {y1 - y0} re S")

    def text(self, x: float, y: float, content: str, size: float = 14, rotation: int = 0) -> None:
        """Texte de `size` points, tourné de 0/90/180/270°, dont le point d'attache est (x, y)."""
        a, b, c, d = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}[rotation % 360]
        escaped = content.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        self.ops.append(f"BT /F1 {size} Tf {a} {b} {c} {d} {x} {self.height - y} Tm ({escaped}) Tj ET")

    def content(self) -> str:
        return "\n".join(self.ops)


def write_pdf(path: str, pages: list[PdfPage]) -> None:
    """Écrit un PDF minimal, sans dépendance : l'import se teste sans PyMuPDF ni reportlab."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # arbre des pages, rempli quand le nombre de pages est connu
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids: list[bytes] = []
    for i, page in enumerate(pages):
        kids.append(b"%d 0 R" % (4 + 2 * i))
        data = page.content().encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Rotate %d "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (str(page.width).encode(), str(page.height).encode(), page.rotation, 5 + 2 * i))
        objects.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(pages)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    with open(path, "wb") as fh:
        fh.write(out)


class BlockTests(unittest.TestCase):
    def test_largest_block_is_the_label(self):
        img = page_with_label()
        rect = importer.auto_crop(img)
        x0, y0, x1, y1 = rect
        self.assertLessEqual(abs(x0 - 300), 6)
        self.assertLessEqual(abs(y0 - 200), 6)
        self.assertLessEqual(abs(x1 - 901), 6)
        self.assertLessEqual(abs(y1 - 1001), 6)

    def test_blank_page_falls_back_to_full_page(self):
        img = Image.new("RGB", (400, 600), "white")
        self.assertEqual(importer.auto_crop(img), (0, 0, 400, 600))

    def test_two_labels_are_two_blocks(self):
        img = page_with_label(box=(100, 100, 600, 800))
        ImageDraw.Draw(img).rectangle((700, 100, 1150, 800), outline="black", width=4)
        blocks = importer.find_blocks(img)
        self.assertEqual(len(blocks), 2)

    def test_two_panels_are_separated_in_spite_of_gutter_caption(self):
        """Colissimo : la légende verticale de la gouttière ne doit pas souder le bordereau et la notice."""
        img = two_panel_page()
        blocks = importer.find_blocks(img)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(len([b for b in blocks if b[2] <= 810]), 1)  # un bloc par panneau
        self.assertEqual(len([b for b in blocks if b[0] >= 830]), 1)
        # Sans texte (image), le plus grand bloc l'emporte ; avec le texte de la notice, le bordereau.
        self.assertGreater(importer.auto_crop(img)[0], 830)
        texts = [("Comment utiliser votre étiquette Colissimo ?", (880, 120, 1620, 190))]
        x0, _, x1, _ = importer.auto_crop(img, texts)
        self.assertLessEqual(x0, 45)
        self.assertLessEqual(x1, 810)

    def test_framed_label_is_not_fragmented(self):
        """Une étiquette encadrée reste d'un seul tenant, même avec deux zones internes comparables."""
        img = Image.new("RGB", (1240, 1754), "white")
        d = ImageDraw.Draw(img)
        d.rectangle((200, 200, 1040, 1200), outline="black", width=4)
        d.rectangle((250, 250, 600, 1000), fill="black")
        d.rectangle((680, 250, 1000, 1000), fill="black")
        blocks = importer.find_blocks(img)
        self.assertEqual(len(blocks), 1)
        x0, y0, x1, y1 = blocks[0]
        self.assertLessEqual(abs(x0 - 200), 6)
        self.assertLessEqual(abs(y0 - 200), 6)
        self.assertLessEqual(abs(x1 - 1041), 6)
        self.assertLessEqual(abs(y1 - 1201), 6)

    def test_tighten_removes_white_margin(self):
        img = page_with_label()
        loose = (200, 100, 1000, 1100)
        x0, y0, x1, y1 = importer.tighten(img, loose)
        self.assertGreaterEqual(x0, 295)
        self.assertGreaterEqual(y0, 195)
        self.assertLessEqual(x1, 905)
        self.assertLessEqual(y1, 1005)


class FitTests(unittest.TestCase):
    def test_portrait_into_100x150(self):
        p = importer.fit_to_label(Image.new("RGB", (600, 800)), 100, 150, margin_mm=2, auto_rotate=True)
        self.assertFalse(p.rotated)
        self.assertLessEqual(p.width_mm, 96.01)
        self.assertLessEqual(p.height_mm, 146.01)
        self.assertAlmostEqual(p.x_mm + p.width_mm / 2, 50, delta=0.05)  # centré
        self.assertAlmostEqual(p.width_mm / p.height_mm, 600 / 800, places=2)  # sans déformation

    def test_landscape_is_rotated_to_use_the_height(self):
        p = importer.fit_to_label(Image.new("RGB", (800, 400)), 100, 150, margin_mm=2, auto_rotate=True)
        self.assertTrue(p.rotated)
        self.assertEqual(p.image.size, (400, 800))
        self.assertAlmostEqual(p.height_mm, 146, delta=0.05)  # limité par la hauteur après rotation

    def test_no_auto_rotate(self):
        p = importer.fit_to_label(Image.new("RGB", (800, 400)), 100, 150, auto_rotate=False)
        self.assertFalse(p.rotated)

    def test_manual_rotation_is_clockwise(self):
        img = Image.new("RGB", (4, 2), "white")
        img.putpixel((0, 0), (0, 0, 0))  # coin haut-gauche noir
        p = importer.fit_to_label(img, 100, 100, auto_rotate=False, extra_rotation=90)
        self.assertEqual(p.image.size, (2, 4))
        self.assertEqual(p.image.getpixel((1, 0)), (0, 0, 0))  # il passe en haut à droite


class PresetTests(unittest.TestCase):
    def test_fraction_roundtrip(self):
        rect = (310, 205, 905, 1010)
        fr = importer.rect_to_fractions(rect, (1240, 1754))
        back = importer.fractions_to_rect(fr, (1240, 1754))
        for a, b in zip(rect, back, strict=True):
            self.assertLessEqual(abs(a - b), 2)

    def test_preset_applies_to_other_resolution(self):
        fr = importer.rect_to_fractions((310, 205, 905, 1010), (1240, 1754))
        rect = importer.fractions_to_rect(fr, (620, 877))  # même page à 150 dpi
        self.assertLessEqual(abs(rect[0] - 155), 2)

    def test_load_presets_tolerates_garbage(self):
        self.assertEqual(importer.load_presets("pas du json"), {})
        self.assertEqual(importer.load_presets(None), {})
        ok = importer.dump_presets({"A": {"rect": [0, 0, 1, 1], "turns": 0}, "B": {"rect": [1]}})
        self.assertEqual(list(importer.load_presets(ok)), ["A"])


class SourceTests(unittest.TestCase):
    def test_png_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.png")
            page_with_label().save(path)
            src = importer.PageSource(path)
            self.assertEqual(src.page_count, 1)
            self.assertEqual(src.render().size, (1240, 1754))

    def test_pdf_source_multipage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.pdf")
            pages = []
            for _ in range(2):
                page = PdfPage()
                page.rect(50, 50, 300, 400, fill=False, thickness=2)
                pages.append(page)
            write_pdf(path, pages)
            src = importer.PageSource(path)
            self.assertEqual(src.page_count, 2)
            img = src.render(1, dpi=150)
            self.assertEqual(img.size, (1240, 1755))  # A4 à 150 dpi
            self.assertIsNotNone(importer.content_bbox(img))
            src.close()

    def test_rotated_page_text_follows_the_display(self):
        """Une page /Rotate 90 est affichée tournée : les rectangles de texte doivent suivre."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rot.pdf")
            page = PdfPage(rotation=90)
            page.text(60, 100, "BORDEREAU", size=14)
            write_pdf(path, [page])
            src = importer.PageSource(path)
            img = src.render(0, importer.PREVIEW_DPI)
            self.assertGreater(img.width, img.height)  # page portrait affichée en paysage
            ink = importer.content_bbox(img)
            self.assertIsNotNone(ink)
            blocks = src.text_blocks(0)
            self.assertEqual(len(blocks), 1)
            text, (x0, y0, _x1, _y1) = blocks[0]
            self.assertIn("BORDEREAU", text)
            # le rectangle du texte tombe sur l'encre réellement dessinée (tolérance : 2,5 mm)
            self.assertLessEqual(abs(x0 - ink[0]), 30)
            self.assertLessEqual(abs(y0 - ink[1]), 30)
            src.close()

    def test_rotation_is_measured_inside_the_label_only(self):
        """Le mode d'emploi de la page est droit : il ne doit pas masquer l'étiquette tournée."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.pdf")
            page = PdfPage()
            page.text(40, 60, "Mode d'emploi de l'etiquette " * 12, size=11)
            page.text(420, 760, "LU / R09 24R RECEIVER", size=16, rotation=90)
            write_pdf(path, [page])
            src = importer.PageSource(path)
            k = importer.PREVIEW_DPI / 72
            label_rect = (int(300 * k), int(200 * k), int(595 * k), int(842 * k))
            self.assertEqual(src.text_rotation(0), 0)  # toute la page : les instructions gagnent
            self.assertEqual(src.text_rotation(0, label_rect), 90)  # l'étiquette, elle, est couchée
            src.close()

    def test_rotation_is_zero_when_the_page_has_no_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vide.pdf")
            write_pdf(path, [PdfPage()])
            src = importer.PageSource(path)
            self.assertEqual(src.text_rotation(), 0)
            src.close()

    def test_placement_adds_the_orientation_fix(self):
        """La correction d'orientation s'ajoute à la rotation demandée par l'utilisateur."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.pdf")
            page = PdfPage()
            page.text(60, 100, "Bordereau", size=14)
            write_pdf(path, [page])
            src = importer.PageSource(path)
            page_img = src.render(0, importer.PREVIEW_DPI)
            rect = importer.auto_crop(page_img, src.text_blocks(0))

            def turns(rotation):
                with mock.patch.object(importer.PageSource, "text_rotation", return_value=rotation):
                    place = importer.build_placement(src, 0, page_img, rect, 100, 150, 2.0, False, 0, 203, False)
                return place.turns

            self.assertEqual((turns(180) - turns(0)) % 4, 2)
            self.assertEqual((turns(90) - turns(0)) % 4, 1)
            src.close()

    def test_rejects_unknown_and_broken_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "x.docx")
            open(bad, "wb").write(b"x")
            with self.assertRaises(importer.ImportError_):
                importer.PageSource(bad)
            broken = os.path.join(tmp, "x.pdf")
            open(broken, "wb").write(b"pas un pdf")
            with self.assertRaises(importer.ImportError_):
                importer.PageSource(broken)


def vector_barcode_pdf(path, module_mm=0.33, text="MONDIAL RELAY Vinted", pages=1):
    """PDF A4 avec un cadre d'étiquette, du texte et un Code 128 vectoriel (barres exactes)."""
    from open_thermal_printer.renderer import barcode_modules

    bits, _ = barcode_modules("code128", "MR0123456789")
    mod = module_mm * 72 / 25.4
    sheets = []
    for _ in range(pages):
        page = PdfPage()
        page.rect(40, 40, 300, 300, fill=False)
        page.text(60, 100, text, size=14)
        for i, bit in enumerate(bits):
            if bit == "1":
                page.rect(60 + i * mod, 120, 60 + (i + 1) * mod, 200, fill=True)
        sheets.append(page)
    write_pdf(path, sheets)


class ModuleSnapTests(unittest.TestCase):
    def test_estimate_module_on_vector_barcode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.pdf")
            vector_barcode_pdf(path)
            src = importer.PageSource(path)
            page = src.render(0)
            m = importer.estimate_module_px(page.crop(importer.auto_crop(page)))
            self.assertIsNotNone(m)
            self.assertAlmostEqual(m, 0.33 / 25.4 * 300, delta=1.0)  # ≈ 3,9 px à 300 dpi
            src.close()

    def test_no_barcode_no_estimate(self):
        self.assertIsNone(importer.estimate_module_px(Image.new("RGB", (300, 300), "white")))

    def test_snap_prefers_integer_dots(self):
        # 2,53 pt idéaux : l'entier inférieur (2) est à -21 %, accepté ; jamais d'agrandissement hors marges
        scale = 96 / 1000  # mm/px
        module_px = 2.53 / (scale * 203 / 25.4)
        new, dots = importer.snap_scale(scale, module_px, 203, 99, 149, 1000, 1500)
        self.assertEqual(dots, 2)
        self.assertLess(new, scale)
        self.assertAlmostEqual(module_px * new * 203 / 25.4, 2.0, places=3)

    def test_snap_small_growth_allowed_when_it_fits(self):
        scale = 50 / 1000
        module_px = 2.95 / (scale * 203 / 25.4)  # 2,95 pt → 3 pt = +1,7 %
        new, dots = importer.snap_scale(scale, module_px, 203, 99, 149, 1000, 1000)
        self.assertEqual(dots, 3)
        self.assertGreater(new, scale)

    def test_snap_gives_up_instead_of_distorting(self):
        scale = 96 / 1000
        module_px = 2.9 / (scale * 203 / 25.4)  # 3 pt = +3,4 % mais ne tient pas ; 2 pt = -31 %
        new, dots = importer.snap_scale(scale, module_px, 203, 96.5, 149, 1000, 1500)
        self.assertIsNone(dots)
        self.assertEqual(new, scale)

    def test_pdf_is_rendered_at_final_resolution(self):
        """Largeur finale = nombre entier de points ; le rendu direct évite un second rééchantillonnage."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.pdf")
            vector_barcode_pdf(path)
            src = importer.PageSource(path)
            page = src.render(0)
            rect = importer.auto_crop(page)
            pl = importer.build_placement(src, 0, page, rect, 100, 150, 2, True, 0, 203, True, {}, cache_key="k")
            self.assertAlmostEqual(pl.width_mm * 203 / 25.4, pl.image.width, delta=0.01)
            self.assertLess(pl.image.width, page.crop(rect).width)  # bien rendu à 203 dpi, pas recadré à 300
            src.close()


class CarrierTests(unittest.TestCase):
    def test_keywords_ignore_case_and_accents(self):
        kws = importer.detect_keywords("Étiquette MONDIAL  RELAY – envoi Vinted ; Colissimo")
        self.assertEqual(kws, ["mondial relay", "colissimo", "vinted"])

    def test_no_false_positive_inside_words(self):
        self.assertEqual(importer.detect_keywords("groups updated gloss"), [])

    def test_label(self):
        self.assertEqual(importer.keywords_label(["mondial relay", "vinted"]), "Mondial Relay + Vinted")
        self.assertEqual(importer.keywords_label(["dpd"]), "DPD")

    def test_match_preset_requires_all_keywords_and_same_page_ratio(self):
        a4 = (1240, 1754)
        presets = {
            "MR": {"rect": [0, 0, 1, 1], "match": ["mondial relay", "vinted"], "ratio": round(1240 / 1754, 4)},
            "MR seul": {"rect": [0, 0, 1, 1], "match": ["mondial relay"], "ratio": round(1240 / 1754, 4)},
            "Sans mots-clés": {"rect": [0, 0, 1, 1]},
        }
        self.assertEqual(importer.match_preset(presets, ["mondial relay", "vinted"], a4), "MR")  # le plus spécifique
        self.assertEqual(importer.match_preset(presets, ["mondial relay"], a4), "MR seul")
        self.assertIsNone(importer.match_preset(presets, ["colissimo"], a4))
        self.assertIsNone(importer.match_preset(presets, ["mondial relay"], (1754, 1240)))  # page paysage
        self.assertIsNone(importer.match_preset({"X": {"rect": [0, 0, 1, 1]}}, ["mondial relay"], a4))

    def test_notice_text_is_recognised(self):
        self.assertTrue(importer.is_notice_text("Preuve de dépôt"))
        self.assertTrue(importer.is_notice_text("COMMENT UTILISER VOTRE ÉTIQUETTE"))
        self.assertTrue(importer.is_notice_text("Mode d’emploi"))
        self.assertFalse(importer.is_notice_text("Destinataire / Recipient\nNum colis : 8B 0014477538 3"))

    def test_pdf_text_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.pdf")
            vector_barcode_pdf(path, text="COLISSIMO Leboncoin")
            src = importer.PageSource(path)
            self.assertEqual(importer.detect_keywords(src.page_text(0)), ["colissimo", "leboncoin"])
            src.close()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "i.png")
            page_with_label().save(path)
            self.assertEqual(importer.PageSource(path).page_text(0), "")  # image : pas de texte


class BatchTests(unittest.TestCase):
    def test_send_jobs_one_connection_all_frames_in_order(self):
        from open_thermal_printer import printer

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            seen = []
            frames = (bytes([65 + i]) * 700 for i in range(3))  # générateur : produit à la volée
            n = printer.send_jobs({"type": "usb", "path": path}, frames, progress=seen.append,
                                  on_job=lambda i, t: seen.append(("job", i, t)), pause=0.01, total=3)
            self.assertEqual(n, 3)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"A" * 700 + b"B" * 700 + b"C" * 700)
            self.assertIn(("job", 3, 3), seen)
            self.assertEqual(seen[-1], 100)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
