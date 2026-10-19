"""Exécution des opérations réseau/périphérique hors du thread de l'interface."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class Worker(QThread):
    """Lance `fn(progress[, message])` dans un thread ; renvoie son résultat ou le message d'erreur.

    `progress(int)` et `message(str)` sont des callbacks thread-safe (signaux Qt) : la fonction peut
    ainsi alimenter la barre de progression et le journal sans toucher à l'interface.
    """

    progress = pyqtSignal(int)
    message = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            wants_message = len(inspect.signature(self._fn).parameters) >= 2
            args = (self.progress.emit, self.message.emit) if wants_message else (self.progress.emit,)
            self.succeeded.emit(self._fn(*args))
        except Exception as exc:  # toute erreur est renvoyée à l'interface, jamais perdue
            self.failed.emit(str(exc) or exc.__class__.__name__)
