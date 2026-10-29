"""Boîte de dialogue des erreurs inattendues : un message court, la trace dans le journal.

Le détail technique n'est pas déversé dans la boîte — une trace Python n'aide pas
l'utilisateur. Elle est dans le journal, et un bouton l'ouvre directement.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from .. import diagnostics

_open: list[QMessageBox] = []  # références gardées : une boîte sans parent serait détruite aussitôt


def _open_journal() -> None:
    diagnostics.logger.info("Ouverture du journal depuis la boîte d'erreur")
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(diagnostics.log_path())))


def build(exc: BaseException, parent: QWidget | None = None) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("Erreur inattendue")
    box.setText("L'opération n'a pas pu aboutir : une erreur inattendue s'est produite.")
    box.setInformativeText(
        f"{exc.__class__.__name__} : {exc}".rstrip()
        + f"\n\nLa trace complète est enregistrée dans :\n{diagnostics.log_path()}"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    journal = box.addButton("Ouvrir le journal", QMessageBox.ButtonRole.ActionRole)

    def open_and_close() -> None:
        _open_journal()
        box.accept()

    journal.clicked.connect(open_and_close)
    return box


def report(exc: BaseException, trace: str = "") -> QMessageBox:
    """Affiche l'erreur sans bloquer l'application ; renvoie la boîte créée.

    `trace` est déjà écrite dans le journal par le crochet : elle n'est pas réaffichée.
    """
    parent = QApplication.activeModalWidget() or QApplication.activeWindow()
    box = build(exc, parent if isinstance(parent, QWidget) else None)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.setModal(False)
    _open.append(box)
    box.finished.connect(lambda _=0, b=box: _open.remove(b) if b in _open else None)
    box.show()
    box.raise_()
    return box
