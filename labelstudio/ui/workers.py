"""Exécution des opérations réseau/périphérique hors du thread de l'interface."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class Worker(QThread):
    """Lance `fn(progress_callback)` dans un thread ; renvoie son résultat ou le message d'erreur."""

    progress = pyqtSignal(int)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[Callable[[int], None]], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.succeeded.emit(self._fn(self.progress.emit))
        except Exception as exc:  # toute erreur est renvoyée à l'interface, jamais perdue
            self.failed.emit(str(exc) or exc.__class__.__name__)
