"""Profils de périphériques : une imprimante nommée et les réglages qui vont avec.

Un même poste peut piloter plusieurs imprimantes — une 4 pouces pour les bordereaux 100 × 150 mm,
une 2 pouces pour les étiquettes de prix 50 × 30 mm — dont les réglages n'ont rien en commun :
résolution (203 ou 300 dpi), noirceur, vitesse, format et espacement. Un profil enregistre ce
couple « appareil + réglages par défaut » pour passer de l'un à l'autre sans tout ressaisir.

Le contenu du document (éléments, textes, images) n'en fait pas partie : appliquer un profil
change la connexion et les réglages d'impression, jamais l'étiquette en cours de composition.

Ce module ne dépend pas de Qt : il ne manipule que des dictionnaires et du JSON, ce qui le rend
testable sans interface.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

# Clés QSettings. La liste est stockée en JSON, comme les préréglages d'import.
PROFILES_KEY = "profiles/list"
LAST_KEY = "profiles/last"

KINDS = ("bluetooth", "usb", "serial")
MEDIA = ("gap", "blackmark", "continuous")
DPI = (203, 300)

# Champs qu'un profil impose au document : les seuls réglages qui dépendent du périphérique.
LABEL_FIELDS = ("width_mm", "height_mm", "gap_mm", "media", "dpi", "density", "speed")


@dataclass
class PrinterProfile:
    """Imprimante nommée, avec les réglages qui lui sont propres."""

    name: str
    kind: str = "bluetooth"
    mac: str = ""
    channel: int = 1
    path: str = ""
    baud: int = 115200
    width_mm: float = 100.0
    height_mm: float = 150.0
    gap_mm: float = 2.0
    media: str = "gap"
    dpi: int = 203
    density: int = 8
    speed: int = 4

    def connection(self) -> dict[str, Any]:
        """Connexion au format attendu par `printer` — le même que `ConnectionPanel.config()`."""
        if self.kind == "bluetooth":
            return {"type": "bluetooth", "mac": self.mac, "channel": self.channel}
        if self.kind == "usb":
            return {"type": "usb", "path": self.path}
        return {"type": "serial", "path": self.path, "baudrate": self.baud}

    def label_defaults(self) -> dict[str, Any]:
        """Réglages d'étiquette propres au périphérique (le reste vient du document)."""
        return {key: getattr(self, key) for key in LABEL_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any, default: float, low: float, high: float) -> float:
    """Nombre borné, tolérant aux valeurs absentes ou aberrantes d'un fichier de réglages."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _dpi(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 203
    return min(DPI, key=lambda option: abs(option - number))  # 203 ou 300, le plus proche


def from_parts(name: str, connection: dict[str, Any], label: dict[str, Any]) -> PrinterProfile:
    """Profil à partir de ce que l'interface connaît : connexion + réglages d'étiquette."""
    kind = _choice(connection.get("type"), KINDS, "bluetooth")
    return PrinterProfile(
        name=name.strip(),
        kind=kind,
        mac=str(connection.get("mac", "")).strip().upper(),
        channel=int(_number(connection.get("channel", 1), 1, 1, 30)),
        path=str(connection.get("path", "")).strip(),
        baud=int(_number(connection.get("baudrate", 115200), 115200, 1200, 921600)),
        width_mm=round(_number(label.get("width_mm"), 100.0, 5.0, 1000.0), 2),
        height_mm=round(_number(label.get("height_mm"), 150.0, 5.0, 1000.0), 2),
        gap_mm=round(_number(label.get("gap_mm"), 2.0, 0.0, 20.0), 1),
        media=_choice(label.get("media"), MEDIA, "gap"),
        dpi=_dpi(label.get("dpi")),
        density=int(_number(label.get("density"), 8, 0, 15)),
        speed=int(_number(label.get("speed"), 4, 1, 10)),
    )


def from_dict(data: Any) -> PrinterProfile | None:
    """Profil relu d'un fichier de réglages, ou None si l'entrée n'est pas exploitable."""
    if not isinstance(data, dict):
        return None
    name = str(data.get("name", "")).strip()
    if not name:
        return None
    kind = _choice(data.get("kind"), KINDS, "bluetooth")
    connection: dict[str, Any] = {"type": kind, "mac": data.get("mac", ""), "channel": data.get("channel", 1),
                                  "path": data.get("path", ""), "baudrate": data.get("baud", 115200)}
    return from_parts(name, connection, data)


def dump_profiles(profiles: dict[str, PrinterProfile]) -> str:
    return json.dumps({name: profile.to_dict() for name, profile in profiles.items()}, ensure_ascii=False)


def load_profiles(text: Any) -> dict[str, PrinterProfile]:
    """Profils relus depuis QSettings ; une entrée illisible est ignorée, sans tout perdre."""
    try:
        data = json.loads(text) if isinstance(text, str) and text else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    profiles: dict[str, PrinterProfile] = {}
    for name, entry in data.items():
        profile = from_dict(entry)
        if profile is not None:
            profiles[str(name).strip()] = profile
    return profiles


def unique_name(profiles: dict[str, PrinterProfile], base: str = "Imprimante") -> str:
    """Nom libre dérivé de `base` : « Imprimante », puis « Imprimante 2 », « Imprimante 3 »…"""
    if base not in profiles:
        return base
    suffix = 2
    while f"{base} {suffix}" in profiles:
        suffix += 1
    return f"{base} {suffix}"


def describe(profile: PrinterProfile) -> str:
    """Résumé d'un profil pour l'interface : appareil, format, résolution, noirceur."""
    if profile.kind == "bluetooth":
        device = profile.mac or "adresse à renseigner"
    else:
        device = profile.path or "port à renseigner"
    return (f"{device} · {profile.width_mm:g} × {profile.height_mm:g} mm · "
            f"{profile.dpi} dpi · noirceur {profile.density}")
