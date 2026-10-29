"""Journal d'erreurs de l'application.

Lancée depuis le menu, l'application n'a pas de terminal : une exception Python dans un
slot ne laisse aucune trace. Tout est donc écrit dans un fichier (rotation comprise), et
les exceptions non rattrapées sont en plus signalées à l'utilisateur par `install_hook`.

Le chemin suit la convention XDG : `$XDG_STATE_HOME/open-thermal-printer/journal.log`,
soit `~/.local/state/...` par défaut.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Callable

APP_FOLDER = "open-thermal-printer"
LOG_NAME = "journal.log"
MAX_BYTES = 512 * 1024  # au-delà, le journal tourne sur deux sauvegardes
BACKUPS = 2
MAX_DIALOGS = 3  # arrête la pluie de boîtes si une exception se répète en boucle

logger = logging.getLogger("open_thermal_printer")
_notified = 0
_active: Path | None = None  # chemin réellement ouvert, s'il a été imposé (tests, ligne de commande)


def state_dir() -> Path:
    """Dossier d'état de l'application (~/.local/state par défaut)."""
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / APP_FOLDER


def log_path() -> Path:
    """Journal en cours d'utilisation (chemin imposé à `setup`, sinon celui de l'utilisateur)."""
    return _active or state_dir() / LOG_NAME


def setup(path: str | Path | None = None, level: int = logging.INFO) -> Path:
    """Ouvre le journal (en créant son dossier) et renvoie son chemin.

    Rappelable sans risque : les gestionnaires précédents sont remplacés, ce qui évite
    les lignes en double quand l'application ouvre le journal plusieurs fois.
    """
    global _active
    target = Path(path) if path is not None else state_dir() / LOG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s : %(message)s"))
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False  # le journal ne doit pas atterrir sur la sortie d'erreur d'un terminal
    _active = target
    return target


def reset() -> None:
    """Referme le journal et revient au chemin par défaut (tests, redémarrage)."""
    global _active
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _active = None


def install_hook(notify: Callable[[BaseException, str], None] | None = None) -> None:
    """Trace les exceptions non rattrapées, puis prévient l'utilisateur via `notify`.

    Vaut pour le fil d'interface comme pour les fils secondaires ; Ctrl+C garde son
    comportement normal. Le compteur de boîtes repart de zéro à chaque installation.
    """
    global _notified
    _notified = 0

    def handle(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        logger.error("Exception non rattrapée :\n%s", text.rstrip())
        global _notified
        if notify is not None and _notified < MAX_DIALOGS:
            _notified += 1
            try:
                notify(exc, text)
            except Exception:  # signaler l'erreur ne doit jamais en provoquer une autre
                logger.exception("Impossible d'afficher l'erreur à l'utilisateur")

    sys.excepthook = handle
    threading.excepthook = lambda args: handle(args.exc_type, args.exc_value, args.exc_traceback)


def restore_hooks() -> None:
    """Rend les crochets par défaut (utilisé par les tests)."""
    sys.excepthook = sys.__excepthook__
    threading.excepthook = threading.__excepthook__
