"""Réglages persistants (QSettings) et reprise de l'ancienne identité de l'application.

L'application s'appelait « Phomemo Label Studio » : renommer l'identité QSettings ferait
disparaître la connexion, le code PIN et les préréglages d'import déjà enregistrés. Ils sont
donc recopiés une fois, au premier démarrage sous le nouveau nom.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings

ORG = "OpenThermalPrinter"
APP = "OpenThermalPrinter"
LEGACY = ("PhomemoLabelStudio", "LabelStudio")
MIGRATED_KEY = "migrated/from_phomemo_label_studio"


def settings() -> QSettings:
    """Réglages de l'application sous son nom actuel."""
    return QSettings(ORG, APP)


def migrate() -> bool:
    """Recopie les réglages de l'ancien nom, une seule fois. Renvoie True si elle a eu lieu.

    Une valeur déjà présente sous le nouveau nom n'est jamais écrasée : la migration ne fait
    que combler ce qui manque.
    """
    current = settings()
    if current.value(MIGRATED_KEY, False, type=bool):
        return False
    legacy = QSettings(*LEGACY)
    keys = legacy.allKeys()
    if not keys:
        return False  # installation neuve : rien à reprendre
    for key in keys:
        if not current.contains(key):
            current.setValue(key, legacy.value(key))
    current.setValue(MIGRATED_KEY, True)
    current.sync()
    return True
