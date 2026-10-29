#!/usr/bin/env python3
"""Point d'entrée : lance Open Thermal Printer."""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from open_thermal_printer import APP_NAME, diagnostics
from open_thermal_printer import settings as app_settings
from open_thermal_printer.ui import error_report
from open_thermal_printer.ui.main_window import MainWindow


def apply_style(app: QApplication) -> None:
    """Habillage commun à l'application et aux captures du README (outils/captures.py)."""
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    # Les icônes ne servent qu'à la barre d'outils : dans les menus, elles nuiraient à la
    # lisibilité en n'apparaissant que sur quelques entrées.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)
    app.setStyleSheet(
        "QToolBar { spacing: 6px; padding: 4px; }"
        "QPushButton { padding: 5px 10px; }"
        "QDockWidget::title { padding: 5px; font-weight: 600; }"
    )


def main() -> int:
    app = QApplication(sys.argv)
    apply_style(app)
    # Renommée « Open Thermal Printer », l'application récupère les réglages de son ancien nom
    # (connexion, code PIN, préréglages d'import) au premier démarrage.
    app_settings.migrate()
    # Sans terminal, une exception ne laisserait aucune trace : journal + boîte de dialogue.
    diagnostics.setup()
    diagnostics.install_hook(error_report.report)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
