"""Tests de l'import d'étiquettes de transport : `python -m unittest discover -s tests -v`"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw

from labelstudio import importer


def page_with_label(box=(300, 200, 900, 1000), size=(1240, 1754)) -> Image.Image:
    """Fausse page A4 : un cadre d'étiquette + deux lignes d'instructions plus bas."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle(box, outline="black", width=4)
    d.rectangle((box[0] + 40, box[1] + 40, box[2] - 40, box[1] + 160), fill="black")  # faux code-barres
    d.rectangle((100, 1500, 1100, 1530), fill="black")  # ligne de texte isolée (trop fine pour être un bloc)
    return img


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
        for a, b in zip(rect, back):
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
        try:
            import pymupdf
        except ImportError:
            self.skipTest("PyMuPDF absent")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.pdf")
            doc = pymupdf.open()
            for _ in range(2):
                page = doc.new_page(width=595, height=842)
                page.draw_rect(pymupdf.Rect(50, 50, 300, 400), width=2)
            doc.save(path)
            doc.close()
            src = importer.PageSource(path)
            self.assertEqual(src.page_count, 2)
            img = src.render(1, dpi=150)
            self.assertEqual(img.size, (1240, 1755))  # A4 à 150 dpi
            self.assertIsNotNone(importer.content_bbox(img))
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
    import pymupdf
    from labelstudio.renderer import barcode_modules

    bits, _ = barcode_modules("code128", "MR0123456789")
    doc = pymupdf.open()
    mod = module_mm * 72 / 25.4
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(40, 40, 300, 300), width=1)
        page.insert_text((60, 100), text, fontsize=14)
        for i, b in enumerate(bits):
            if b == "1":
                page.draw_rect(pymupdf.Rect(60 + i * mod, 120, 60 + (i + 1) * mod, 200), fill=(0, 0, 0), color=None)
    doc.save(path)
    doc.close()


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
        from labelstudio import printer

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
