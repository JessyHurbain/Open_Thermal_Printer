"""Génère les captures du README à partir de l'application réelle, sans écran ni imprimante.

    ./captures.sh              # les quatre images de docs/screenshots
    ./captures.sh import       # une seule (editeur, element, connexion, import)

Rien n'est fabriqué à la main : les fenêtres sont celles du programme, rendues par Qt en mode
« offscreen ». Deux précautions rendent l'opération inoffensive et reproductible :
  - QSettings pointe vers un dossier temporaire, la configuration de l'utilisateur est intacte ;
  - la découverte Bluetooth est simulée (un appareil fictif), donc aucun matériel n'est requis.

Le bordereau de démonstration de la capture d'import est dessiné ici même : noms, adresses et
numéro de suivi sont inventés.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # avant toute création de QApplication

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from labelstudio import printer, templates  # noqa: E402
from labelstudio.ui.import_dialog import ImportDialog  # noqa: E402
from labelstudio.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
SHOTS = ("editeur", "element", "connexion", "import")
DEMO_DEVICE = ("AA:BB:CC:DD:EE:FF", "PM-241-BT")  # appareil fictif de la capture « Connexion »


# ---------------------------------------------------------------- outils
def pump(app: QApplication, seconds: float) -> None:
    """Laisse Qt traiter les événements (peinture, minuteurs) pendant un instant."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@contextmanager
def settings_sandbox():
    """Redirige QSettings vers un dossier temporaire (les deux formats : Qt résout via Native)."""
    with tempfile.TemporaryDirectory() as tmp:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        for fmt in (QSettings.Format.IniFormat, QSettings.Format.NativeFormat):
            QSettings.setPath(fmt, QSettings.Scope.UserScope, tmp)
        yield


@contextmanager
def demo_hardware():
    """Remplace la découverte matérielle : un seul appareil appairé, fictif.

    La vérification périodique est neutralisée : l'appareil fictif étant sélectionné, elle
    ouvrirait une vraie connexion RFCOMM vers cette adresse à chaque capture.
    """
    patches = [
        mock.patch.object(printer, "list_paired_devices", return_value=[DEMO_DEVICE]),
        mock.patch.object(printer, "list_usb_devices", return_value=[]),
        mock.patch.object(printer, "list_serial_ports", return_value=[]),
        mock.patch.object(MainWindow, "check_printer"),
    ]
    for patch in patches:
        patch.start()
    try:
        yield
    finally:
        for patch in reversed(patches):
            patch.stop()


def window(app: QApplication) -> MainWindow:
    """Fenêtre principale prête à être capturée (état de l'imprimante déjà stabilisé)."""
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    pump(app, 1.6)  # laisse la première vérification d'imprimante poser « non configurée »
    return win


def grab(widget: QWidget, name: str) -> Path:
    path = OUT / f"{name}.png"
    widget.grab().save(str(path), "PNG")
    print(f"  ✔ {path.relative_to(ROOT)}")
    return path


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def demo_bordereau(path: Path) -> Path:
    """Bordereau 100 % fictif : mêmes repères visuels qu'un vrai (cadre, code-barres, adresses)."""
    width, height = 900, 1300
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((12, 12, width - 13, height - 13), outline="black", width=3)
    draw.text((50, 55), "MONDIAL RELAY", font=font(54, True), fill="black")
    draw.text((50, 125), "Bordereau d'expédition", font=font(28), fill="black")
    x, step = 50, 0
    while x < 520:  # code-barres décoratif, largeurs variées mais reproductibles
        bar = (3, 3, 5, 8, 13)[step % 5]
        draw.rectangle((x, 220, x + bar - 1, 430), fill="black")
        x += bar + (4, 5, 7)[step % 3]
        step += 1
    draw.text((50, 450), "8B 0099001122 3", font=font(38, True), fill="black")
    draw.text((50, 560), "DESTINATAIRE", font=font(24), fill="black")
    draw.text((50, 600), "CAMILLE MARTIN", font=font(40, True), fill="black")
    draw.text((50, 650), "8 RUE DES TILLEULS", font=font(32), fill="black")
    draw.text((50, 692), "69003 LYON", font=font(36, True), fill="black")
    draw.text((50, 800), "EXPÉDITEUR", font=font(24), fill="black")
    draw.text((50, 840), "Atelier Démo", font=font(30), fill="black")
    draw.text((50, 878), "14 QUAI DES ARTISANS", font=font(30), fill="black")
    draw.text((50, 916), "44000 NANTES", font=font(30), fill="black")
    draw.text((50, 1050), "29/10/26 09:21   WS_SLS 26100502", font=font(24), fill="black")
    img.save(path)
    return path


# ---------------------------------------------------------------- captures
def shot_editeur(app: QApplication) -> None:
    """Éditeur : modèle d'expédition, liste des éléments, onglet Étiquette, commandes TSPL."""
    win = window(app)
    try:
        win.right_tabs.setCurrentIndex(0)
        pump(app, 0.4)
        grab(win, "editeur")
    finally:
        win.close()
        win.deleteLater()


def shot_element(app: QApplication) -> None:
    """Un code-barres sélectionné, panneau Élément ouvert sur ses propriétés."""
    win = window(app)
    try:
        row = next((i for i, el in enumerate(win.label.elements) if el.kind == "barcode"), -1)
        win._select(row)  # sélectionne sur l'aperçu et bascule sur l'onglet Élément
        pump(app, 0.5)
        grab(win, "element")
    finally:
        win.close()
        win.deleteLater()


def shot_connexion(app: QApplication) -> None:
    """Onglet Connexion : appareil appairé simulé, adresse, canal, boutons d'appairage."""
    win = window(app)
    try:
        panel = win.conn_panel
        win.right_tabs.setCurrentIndex(2)
        panel.kind.setCurrentIndex(max(0, panel.kind.findData("bluetooth")))
        panel.mac.setText(DEMO_DEVICE[0])
        panel.channel.setValue(1)
        panel.refresh_devices()
        pump(app, 0.4)
        grab(win, "connexion")
    finally:
        win.close()
        win.deleteLater()


def shot_import(app: QApplication) -> None:
    """Boîte d'import ouverte sur le bordereau fictif, rognage et aperçu calculés."""
    with tempfile.TemporaryDirectory() as tmp:
        demo = demo_bordereau(Path(tmp) / "bordereau_demo.png")
        dialog = ImportDialog(templates.blank().settings, [str(demo)])
        dialog.resize(1150, 848)
        dialog.show()
        try:
            end = time.monotonic() + 15
            while time.monotonic() < end and not (dialog.items and dialog.view.crop):
                app.processEvents()
                time.sleep(0.03)
            pump(app, 2.5)  # laisse l'aperçu final se calculer
            grab(dialog, "import")
        finally:
            dialog.close()
            dialog.deleteLater()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Régénère les captures du README.")
    parser.add_argument("shots", nargs="*", choices=SHOTS,
                        help="captures à produire (défaut : toutes)")
    args = parser.parse_args(argv)
    wanted = args.shots or list(SHOTS)

    OUT.mkdir(parents=True, exist_ok=True)
    with settings_sandbox(), demo_hardware():
        app = QApplication.instance() or QApplication([])
        from main import apply_style  # même habillage que l'application lancée normalement

        apply_style(app)
        print(f"Captures dans {OUT.relative_to(ROOT)} :")
        for name, shot in (("editeur", shot_editeur), ("element", shot_element),
                           ("connexion", shot_connexion), ("import", shot_import)):
            if name in wanted:
                shot(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
