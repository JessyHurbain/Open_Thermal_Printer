"""Connexion à l'imprimante : Bluetooth RFCOMM, USB (/dev/usb/lpN) ou port série.

Le Bluetooth passe par les sockets RFCOMM de la bibliothèque standard Python
(`socket.AF_BLUETOOTH`) : aucune dépendance externe (PyBluez n'est plus maintenu et ne
s'installe plus avec les Python récents) et CUPS n'intervient pas du tout.

Config de connexion (dict) :
    {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF", "channel": 1}
    {"type": "usb",       "path": "/dev/usb/lp0"}
    {"type": "serial",    "path": "/dev/ttyUSB0", "baudrate": 115200}
"""

from __future__ import annotations

import errno
import glob
import os
import re
import select
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

CHUNK_SIZE = 512  # octets par écriture : évite de saturer le tampon de l'imprimante
CHUNK_DELAY = 0.004  # pause entre deux paquets (s)
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

ProgressCb = Callable[[int], None]  # reçoit un pourcentage 0..100


class PrinterError(Exception):
    """Erreur de communication avec un message directement affichable à l'utilisateur."""


# ================================================================== connexions
class Connection(ABC):
    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def _write(self, chunk: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        """Lit jusqu'à `size` octets ; renvoie b'' si rien n'arrive avant `timeout`."""

    def check_alive(self) -> bool:
        """False si le périphérique a fermé la liaison (utile pour un lien gardé ouvert).

        Sans information contraire, une liaison ouverte est considérée vivante : les
        transports USB et série le signalent d'eux-mêmes en erreur d'écriture.
        """
        return True

    def send(self, data: bytes, progress: ProgressCb | None = None, settle: float = 0.4) -> None:
        total = len(data) or 1
        for pos in range(0, len(data), CHUNK_SIZE):
            self._write(data[pos : pos + CHUNK_SIZE])
            if progress:
                progress(min(100, int((pos + CHUNK_SIZE) * 100 / total)))
            time.sleep(CHUNK_DELAY)
        time.sleep(settle)  # laisse l'imprimante vider son tampon (avant fermeture ou étiquette suivante)
        if progress:
            progress(100)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


class BluetoothConnection(Connection):
    def __init__(self, mac: str, channel: int = 1, timeout: float = 10.0):
        if not MAC_RE.match(mac or ""):
            raise PrinterError("Adresse MAC invalide (format attendu : AA:BB:CC:DD:EE:FF).")
        self.mac, self.channel, self.timeout = mac.upper(), int(channel), timeout
        self.sock: socket.socket | None = None

    def open(self) -> None:
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise PrinterError(
                "Ce Python a été compilé sans support Bluetooth.\n"
                "Utilisez le Python du système (/usr/bin/python3) et installez : sudo apt install bluez libbluetooth-dev"
            )
        # Deux essais : juste après un envoi, BlueZ peut garder le lien quelques instants et
        # refuser la reconnexion (EBUSY) alors que rien d'autre n'utilise l'imprimante.
        for attempt in (0, 1):
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(self.timeout)
            try:
                sock.connect((self.mac, self.channel))
            except OSError as exc:
                sock.close()
                if attempt == 0 and exc.errno in (errno.EBUSY, errno.EADDRINUSE, errno.EAGAIN):
                    time.sleep(0.6)
                    continue
                raise PrinterError(self._explain(exc)) from exc
            self.sock = sock
            return

    def _explain(self, exc: OSError) -> str:
        hints = {
            errno.ECONNREFUSED: "Connexion refusée : mauvais canal RFCOMM, ou imprimante déjà connectée à un autre appareil (téléphone ?).",
            errno.EHOSTDOWN: "Imprimante injoignable : est-elle allumée et à portée ?",
            errno.EHOSTUNREACH: "Imprimante injoignable : est-elle allumée et à portée ?",
            errno.EBUSY: "Connexion déjà utilisée par un autre programme (rfcomm bind, CUPS, autre instance).",
            errno.EACCES: "Permission refusée pour le Bluetooth.",
            errno.ETIMEDOUT: "Délai dépassé : l'imprimante ne répond pas (éteinte, hors de portée ou non appairée).",
        }
        detail = hints.get(exc.errno) or ("Délai dépassé." if isinstance(exc, socket.timeout) else str(exc))
        return f"Bluetooth {self.mac} (canal {self.channel}) : {detail}"

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _write(self, chunk: bytes) -> None:
        try:
            self.sock.sendall(chunk)
        except OSError as exc:
            raise PrinterError(f"Écriture Bluetooth interrompue : {exc}") from exc

    def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        ready, _, _ = select.select([self.sock], [], [], timeout)
        if not ready:
            return b""
        try:
            return self.sock.recv(size)
        except OSError:
            return b""

    def check_alive(self) -> bool:
        """Distingue « rien à lire » (lien vivant) d'une fin de flux, seule trace d'une déconnexion."""
        if self.sock is None:
            return False
        try:
            ready, _, _ = select.select([self.sock], [], [], 0)
            if not ready:
                return True
            return bool(self.sock.recv(1, socket.MSG_PEEK))
        except OSError:
            return False


class UsbLpConnection(Connection):
    """Périphérique caractère /dev/usb/lpN (module noyau usblp)."""

    def __init__(self, path: str = "/dev/usb/lp0"):
        self.path, self.fd, self.can_read = path, None, False

    def open(self) -> None:
        try:
            try:
                self.fd, self.can_read = os.open(self.path, os.O_RDWR), True
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.ENOENT, errno.ENODEV):
                    raise
                self.fd, self.can_read = os.open(self.path, os.O_WRONLY), False
        except OSError as exc:
            if exc.errno == errno.EACCES:
                msg = f"Permission refusée sur {self.path}. Ajoutez-vous au groupe lp :  sudo usermod -aG lp $USER  (puis reconnectez-vous)."
            elif exc.errno in (errno.ENOENT, errno.ENODEV):
                msg = f"{self.path} introuvable : imprimante éteinte, câble USB débranché, ou module usblp non chargé."
            else:
                msg = f"Impossible d'ouvrir {self.path} : {exc}"
            raise PrinterError(msg) from exc

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _write(self, chunk: bytes) -> None:
        try:
            view = memoryview(chunk)
            while view:
                view = view[os.write(self.fd, view) :]
        except OSError as exc:
            raise PrinterError(f"Écriture USB interrompue : {exc}") from exc

    def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        if not self.can_read:
            return b""
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return b""
        try:
            return os.read(self.fd, size)
        except OSError:
            return b""


class SerialConnection(Connection):
    """Port série (/dev/ttyUSB0, /dev/ttyACM0, /dev/rfcomm0…) via pyserial."""

    def __init__(self, path: str = "/dev/ttyUSB0", baudrate: int = 115200):
        self.path, self.baudrate, self.ser = path, int(baudrate), None

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise PrinterError("pyserial n'est pas installé :  pip install pyserial") from exc
        try:
            self.ser = serial.Serial(self.path, self.baudrate, timeout=0.2, write_timeout=10)
        except serial.SerialException as exc:
            text = str(exc)
            if "Permission" in text:
                text = f"Permission refusée sur {self.path}. Ajoutez-vous au groupe dialout :  sudo usermod -aG dialout $USER  (puis reconnectez-vous)."
            raise PrinterError(f"Port série {self.path} : {text}") from exc

    def close(self) -> None:
        if self.ser:
            self.ser.close()
            self.ser = None

    def _write(self, chunk: bytes) -> None:
        try:
            self.ser.write(chunk)
            self.ser.flush()
        except Exception as exc:
            raise PrinterError(f"Écriture série interrompue : {exc}") from exc

    def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        self.ser.timeout = timeout
        return self.ser.read(size)


def make_connection(cfg: dict, timeout: float | None = None) -> Connection:
    """Connexion correspondant à `cfg`. `timeout` ne concerne que l'ouverture Bluetooth."""
    kind = cfg.get("type")
    if kind == "bluetooth":
        return BluetoothConnection(cfg.get("mac", ""), cfg.get("channel", 1),
                                   timeout if timeout is not None else 10.0)
    if kind == "usb":
        return UsbLpConnection(cfg.get("path") or "/dev/usb/lp0")
    if kind == "serial":
        return SerialConnection(cfg.get("path") or "/dev/ttyUSB0", cfg.get("baudrate", 115200))
    raise PrinterError(f"Type de connexion inconnu : {kind!r}")


# ================================================================== découverte
# Noms qui désignent l'imprimante : modèles Phomemo d'abord, puis mots-clés génériques.
PHOMEMO_HINTS = ("PHOMEMO", "PM-241", "PM241", "PM-2", "PM2")
PRINTER_HINTS = ("PRINTER", "IMPRIMANTE", "THERMAL", "LABEL", "ETIQUET", "ZEBRA", "BROTHER", "TSC", "GODEX")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(output: str) -> str:
    """Sortie de bluetoothctl sans ses séquences de couleur.

    bluetoothctl colore ses lignes (« [ESC[0;92mNEWESC[0m] Device … » quand la sortie n'est pas
    un terminal) : sans ce nettoyage aucune ligne de scan n'est reconnue et l'imprimante semble
    absente alors qu'elle est bien annoncée.
    """
    return _ANSI_RE.sub("", output)


def _parse_devices(output: str) -> list[tuple[str, str]]:
    """Lignes « Device AA:BB:CC:DD:EE:FF Nom » de bluetoothctl -> liste de (MAC, nom).

    Un scan actif préfixe les lignes par « [NEW] » ou « [CHG] », avec ou sans couleurs : on les
    accepte aussi.
    """
    found = re.findall(r"^\s*(?:\[[A-Z]+\]\s*)?Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(.*)$",
                       _plain(output), re.M)
    return [(mac.upper(), name.strip()) for mac, name in found]


@dataclass(frozen=True)
class Nearby:
    """Appareil Bluetooth repéré à portée."""

    mac: str
    name: str
    printer: bool = False  # la classe ou l'icône annoncée par l'appareil dit « imprimante »


_PROPERTY_RE = re.compile(r"^(Class|Icon|UUID|RSSI|TxPower|AddressType|ManufacturerData|ServiceData|Name|Alias):")


def _announced_printers(output: str) -> set[str]:
    """MAC des appareils dont BlueZ annonce l'icône « printer » ou la classe « imprimante ».

    Une Phomemo ne porte pas forcément son nom dans son nom Bluetooth (« PM-241-BT ») :
    la classe principale 0x06 (Imaging) et l'icône sont des indices bien plus fiables.
    Les deux formes de sortie sont acceptées : propriété sur la ligne du scan
    (« Device MAC Icon: printer ») ou sur sa propre ligne (« info » : un en-tête puis des
    propriétés indentées).
    """
    macs: set[str] = set()
    current = ""
    for line in _plain(output).splitlines():
        header = re.search(r"Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", line)
        if header:
            current = header.group(1).upper()
        prop = re.search(r"\b(Icon|Class):\s*(\S+)", line)
        if not prop or not current:
            continue
        kind, value = prop.group(1), prop.group(2)
        if kind == "Icon" and value.lower() == "printer":
            macs.add(current)
        elif kind == "Class":
            try:
                major = (int(value, 16) >> 8) & 0x1F  # classe principale (bits 8 à 12)
            except ValueError:
                continue
            if major == 0x06:  # Imaging : imprimante, scanner…
                macs.add(current)
    return macs


def _parse_nearby(output: str) -> list[Nearby]:
    """Appareils repérés (ordre d'apparition), avec le nom et l'indice « imprimante ».

    Un appareil produit plusieurs lignes (« Class: », « Icon: », « RSSI: »…) : seule la première
    ligne qui porte un vrai nom compte, les lignes de propriétés ne deviennent jamais un nom.
    """
    printers = _announced_printers(output)
    order: list[str] = []
    names: dict[str, str] = {}
    for mac, name in _parse_devices(output):
        if name.startswith(("Name:", "Alias:")):
            name = name.split(":", 1)[1].strip()
        elif _PROPERTY_RE.match(name):
            name = ""  # ligne de propriété, pas un nom
        if mac not in order:
            order.append(mac)
        if name and not names.get(mac):
            names[mac] = name
    return [Nearby(mac, names.get(mac, ""), mac in printers) for mac in order]


def list_paired_devices() -> list[tuple[str, str]]:
    """Appareils Bluetooth connus de BlueZ : liste de (adresse MAC, nom)."""
    for cmd in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "paired-devices"], ["bluetoothctl", "devices"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        found = _parse_devices(out)
        if found:
            return found
    return []


def discover_bluetooth_devices(timeout: float = 6.0) -> list[Nearby]:
    """Scan Bluetooth actif borné dans le temps : appareils à portée, appairés ou non.

    La sortie du scan est complétée par les appareils déjà connus de BlueZ : un appareil connu
    n'est pas réannoncé pendant un scan, et il reste pourtant utilisable (donc à afficher).
    Vide si BlueZ est absent ou refuse le scan : la détection continue avec le reste.
    """
    chunks: list[str] = []
    try:
        scan = subprocess.run(
            ["bluetoothctl", "--timeout", str(max(1, int(timeout))), "scan", "on"],
            capture_output=True, text=True, timeout=timeout + 8, check=False,
        )
        chunks += [scan.stdout, scan.stderr]
        known = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True,
                               timeout=5, check=False)
        chunks.append(known.stdout)
    except (OSError, subprocess.SubprocessError):
        return []
    nearby = _parse_nearby("".join(chunks))
    # Un appareil déjà connu n'est plus décrit pendant un scan : sa classe est alors relue
    # une fois, pour savoir s'il s'agit d'une imprimante sans se fier à son seul nom.
    unknown = [device.mac for device in nearby if not device.printer]
    if unknown:
        details = "".join(_device_info(mac) for mac in unknown)
        printers = _announced_printers(details)
        nearby = [Nearby(d.mac, d.name, d.printer or d.mac in printers) for d in nearby]
    return nearby


def _device_info(mac: str) -> str:
    """Sortie de `bluetoothctl info MAC` (classe, icône, nom) ; vide si indisponible."""
    try:
        return subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True,
                              timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def bluetooth_problem() -> str:
    """Raison lisible pour laquelle un scan ne peut rien trouver (vide si tout semble normal)."""
    try:
        show = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True,
                              timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return "bluetoothctl est introuvable : installez le paquet bluez."
    show = _plain(show)
    if not show.strip() or "no default controller" in show.lower():
        return "Aucun adaptateur Bluetooth actif sur cette machine."
    if re.search(r"^\s*Powered:\s*no", show, re.M):
        return "L'adaptateur Bluetooth est éteint : activez-le (menu Bluetooth de Linux Mint), " \
               "puis relancez la recherche."
    return ""


def list_serial_ports() -> list[str]:
    patterns = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/rfcomm*")
    return sorted(p for pat in patterns for p in glob.glob(pat))


def list_usb_devices() -> list[str]:
    return sorted(glob.glob("/dev/usb/lp*"))


def _usb_description(path: str) -> str:
    """Fabricant + modèle lus dans sysfs pour un /dev/usb/lpN (vide si indisponible)."""
    base = os.path.join("/sys/class/usbmisc", os.path.basename(path), "device", "..")
    parts = []
    for attr in ("manufacturer", "product"):
        try:
            with open(os.path.join(base, attr), encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
        except OSError:
            continue
        if text:
            parts.append(text)
    return " ".join(dict.fromkeys(parts))


def _has_hint(text: str, hints: tuple[str, ...]) -> bool:
    upper = text.upper()
    return any(hint in upper for hint in hints)


def looks_like_printer(name: str) -> bool:
    """Vrai si le nom d'un appareil évoque une imprimante (Phomemo ou nom générique)."""
    return _has_hint(name, PHOMEMO_HINTS + PRINTER_HINTS)


@dataclass(frozen=True)
class Device:
    """Imprimante potentielle repérée sur la machine."""

    kind: str  # bluetooth | usb | serial
    label: str  # description affichée
    config: dict  # config prête pour make_connection()
    score: int  # plus haut = plus probable ; 0 = repéré mais inutilisable (non appairé)
    name: str = ""  # nom Bluetooth de l'appareil
    pending: bool = False  # repéré à portée mais pas appairé : à appairer avant utilisation

    @property
    def usable(self) -> bool:
        return self.score > 0


def _paired_printer_hints(paired: list[tuple[str, str]]) -> set[str]:
    """MAC des appareils appairés dont la classe/icône Bluetooth dit « imprimante »."""
    return _announced_printers("".join(_device_info(mac) for mac, _name in paired))


def build_devices(paired: list[tuple[str, str]], usb_paths: list[str], serial_paths: list[str],
                  discovered: list[Nearby] | None = None, printer_macs: set[str] | None = None) -> list[Device]:
    """Imprimantes candidates, les plus probables d'abord.

    Une Phomemo appairée passe devant les autres appareils Bluetooth, un port USB devant la série
    (plus fiable, pas d'appairage). Un appareil appairé dont le nom n'évoque pas une imprimante
    (casque, enceinte…) est signalé mais jamais retenu : il reste choisissable à la main.
    `discovered` : appareils à portée mais non appairés, à appairer avant utilisation.
    `printer_macs` : adresses dont la classe Bluetooth annonce une imprimante, quand le nom
    n'en dit rien (« PM-241-BT » par exemple).
    """
    devices: list[Device] = []
    printer_macs = printer_macs or set()
    for mac, name in paired:
        config = {"type": "bluetooth", "mac": mac, "channel": 1}
        if _has_hint(f"{name} {mac}", PHOMEMO_HINTS):
            score = 90
        elif looks_like_printer(name):
            score = 80
        elif mac in printer_macs:
            score = 85  # classe/icône « imprimante » : plus sûr qu'un nom absent
        else:
            score = 0
        label = f"Bluetooth {name or mac} ({mac})"
        devices.append(Device("bluetooth", label if score else f"{label} — appairé, pas une imprimante identifiée",
                              config, score, name))
    for path in usb_paths:
        description = _usb_description(path)
        score = 95 if _has_hint(description, PHOMEMO_HINTS) else 70
        devices.append(Device("usb", f"USB {path}" + (f" — {description}" if description else ""),
                              {"type": "usb", "path": path}, score))
    for path in serial_paths:
        rfcomm = "rfcomm" in os.path.basename(path)  # pont Bluetooth déjà établi
        devices.append(Device("serial", f"Port série {path}",
                              {"type": "serial", "path": path, "baudrate": 115200}, 60 if rfcomm else 50))
    known = {d.config.get("mac") for d in devices if d.kind == "bluetooth"}
    for nearby in discovered or []:
        if nearby.mac in known:
            continue
        suffix = " — non appairé, imprimante probable" if nearby.printer else " — non appairé"
        devices.append(Device("bluetooth", f"Bluetooth {nearby.name or nearby.mac} ({nearby.mac}){suffix}",
                              {"type": "bluetooth", "mac": nearby.mac, "channel": 1}, 0,
                              nearby.name, pending=True))
    devices.sort(key=lambda d: (-d.score, d.label))
    return devices


def scan_devices(discover: bool = True) -> list[Device]:
    """Imprimantes potentielles : Bluetooth appairées, USB (usblp) et ports série.

    Si rien d'utilisable n'est trouvé, un scan Bluetooth à portée signale l'imprimante à appairer.
    """
    paired, usb, serial = list_paired_devices(), list_usb_devices(), list_serial_ports()
    hints = _paired_printer_hints(paired)
    devices = build_devices(paired, usb, serial, printer_macs=hints)
    if discover and not any(d.usable for d in devices):
        devices = build_devices(paired, usb, serial, discover_bluetooth_devices(), hints)
    return devices


# ================================================================== opérations de haut niveau
@dataclass
class TestResult:
    ok: bool
    message: str


# Octet renvoyé par <ESC>!? en TSPL (plusieurs bits peuvent être levés)
_TSPL_STATUS = {
    0x01: "tête d'impression ouverte",
    0x02: "bourrage papier",
    0x04: "plus de papier / étiquettes",
    0x08: "plus de ruban",
    0x10: "imprimante en pause",
    0x20: "impression en cours",
    0x40: "capot ouvert",
    0x80: "autre erreur",
}


def query_status(conn: Connection, language: str = "tspl") -> TestResult:
    """Interroge une liaison déjà ouverte (statut TSPL ou ZPL). Lève `PrinterError` si elle casse."""
    lines = ["Liaison établie."]
    if language == "zpl":
        conn._write(b"~HS")
        reply = conn.read(128, 1.5)
        if reply:
            lines.append("Statut ZPL reçu : " + reply.decode("ascii", "replace").strip().replace("\r\n", " | "))
        else:
            lines.append("Aucune réponse au statut (~HS) — fréquent sur les imprimantes compatibles.")
        return TestResult(True, "\n".join(lines))

    conn._write(b"\x1b!?")  # statut : 1 octet
    status = conn.read(1, 1.5)
    if status:
        code = status[0]
        if code == 0:
            lines.append("État : prête.")
        else:
            problems = [txt for bit, txt in _TSPL_STATUS.items() if code & bit]
            lines.append("État : " + ", ".join(problems) + f" (0x{code:02X}).")
            if code & 0x20 and not code & 0xDF:
                pass  # simple impression en cours : pas une anomalie
        ok = code in (0x00, 0x20)
    else:
        if not conn.check_alive():
            raise PrinterError("Liaison fermée par l'imprimante : éteinte, hors de portée ou déconnectée.")
        lines.append("Aucune réponse au statut (<ESC>!?) — la liaison fonctionne mais ce modèle ne répond peut-être pas.")
        ok = True
    conn._write(b"~!T\r\n")  # nom du modèle
    model = conn.read(64, 1.0).decode("ascii", "replace").strip()
    if model:
        lines.append(f"Modèle : {model}")
    return TestResult(ok, "\n".join(lines))


def test_connection(cfg: dict, language: str = "tspl", timeout: float | None = None) -> TestResult:
    """Ouvre la liaison puis interroge l'imprimante (statut TSPL ou ZPL).

    `timeout` : délai d'ouverture (Bluetooth) ; court pour une vérification périodique.
    """
    try:
        with make_connection(cfg, timeout) as conn:
            return query_status(conn, language)
    except PrinterError as exc:
        return TestResult(False, str(exc))


class StatusProbe:
    """Liaison de surveillance gardée ouverte entre deux vérifications.

    Rouvrir une liaison Bluetooth à chaque vérification fait clignoter la connexion
    côté BlueZ : le bureau (Cinnamon, GNOME) affiche alors une notification de
    connexion puis de déconnexion, en boucle. La liaison est donc conservée, et
    refaite seulement après un échec ou un changement de configuration.

    `factory` est injectable pour les tests ; elle reçoit (cfg, timeout).
    """

    def __init__(self, cfg: dict, language: str = "tspl", timeout: float = 3.0,
                 factory: Callable[[dict, float | None], Connection] = make_connection):
        self.cfg, self.language, self.timeout = dict(cfg), language, timeout
        self._factory = factory
        self._conn: Connection | None = None
        # Seul le Bluetooth clignote côté BlueZ : USB et série sont ouverts à la demande
        # pour ne pas garder le périphérique occupé entre deux vérifications.
        self._keep = self.cfg.get("type") == "bluetooth"

    def matches(self, cfg: dict, language: str) -> bool:
        """Vrai si la liaison ouverte correspond encore à la configuration demandée."""
        return self.language == language and self.cfg == dict(cfg)

    def check(self) -> TestResult:
        """Interroge l'imprimante, en rouvrant la liaison une fois si elle est devenue muette."""
        if self._conn is None:
            return self._open_and_query()
        try:
            return query_status(self._conn, self.language)
        except (PrinterError, OSError):
            self.close()  # lien mort (imprimante éteinte, appairage perdu, câble retiré…)
        return self._open_and_query()

    def _open_and_query(self) -> TestResult:
        try:
            self._conn = self._factory(self.cfg, self.timeout)
            self._conn.open()
            result = query_status(self._conn, self.language)
        except (PrinterError, OSError) as exc:
            self.close()
            return TestResult(False, str(exc) if isinstance(exc, PrinterError) else f"Liaison interrompue : {exc}")
        if not self._keep:
            self.close()
        return result

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def send_job(cfg: dict, data: bytes, progress: ProgressCb | None = None) -> None:
    with make_connection(cfg) as conn:
        conn.send(data, progress)


def send_jobs(cfg: dict, jobs, progress: ProgressCb | None = None,
              on_job: Callable[[int, int], None] | None = None, pause: float = 1.0, total: int | None = None) -> int:
    """Envoie plusieurs travaux à la suite sur UNE SEULE connexion (pas de reconnexion Bluetooth entre
    les étiquettes). `jobs` peut être un itérateur : chaque trame est produite juste avant son envoi,
    ce qui permet de rendre la suivante pendant que l'imprimante finit la précédente.
    Renvoie le nombre d'étiquettes envoyées.
    """
    jobs = list(jobs) if isinstance(jobs, (list, tuple)) else jobs
    total = total or (len(jobs) if hasattr(jobs, "__len__") else 0)
    sent = 0
    with make_connection(cfg) as conn:
        for index, data in enumerate(jobs):
            if on_job:
                on_job(index + 1, total)
            share = (lambda pct, i=index: progress(int((i * 100 + pct) / max(1, total)))) if progress and total else None
            conn.send(data, share, settle=pause)
            sent += 1
    return sent


def send_selftest(cfg: dict, language: str = "tspl") -> None:
    """Demande à l'imprimante d'imprimer sa propre page de configuration."""
    payload = b"SELFTEST\r\n" if language == "tspl" else b"~WC\r\n"
    send_job(cfg, payload)


# ================================================================== détection automatique
@dataclass
class Detection:
    """Détection automatique : appareils repérés, candidat retenu et résultat du test de liaison."""

    devices: list[Device]
    chosen: Device | None = None
    result: TestResult | None = None

    def summary(self) -> str:
        """Phrase affichable dans la barre d'état."""
        if not self.devices:
            return ("Aucune imprimante détectée : allumez-la, appairez-la en Bluetooth "
                    "ou branchez-la en USB, puis relancez la détection.")
        if self.chosen is None:
            pending = [d.label for d in self.devices if "imprimante probable" in d.label]
            if pending:
                return (f"Imprimante détectée mais pas encore appairée : {'; '.join(pending)}. "
                        "Appairez-la avec le bouton « Appairer… », puis relancez la détection.")
            names = ", ".join(d.label for d in self.devices)
            if all("non appairé" in d.label for d in self.devices):
                return (f"Aucune imprimante appairée. À portée : {names}. "
                        "Appairez-la avec le bouton « Appairer… », puis relancez la détection.")
            return (f"Aucune imprimante reconnue automatiquement. Repéré : {names}. "
                    "Choisissez le bon port dans l'onglet Connexion, ou branchez l'imprimante en USB.")
        if self.result and self.result.ok:
            first = self.result.message.splitlines()[0] if self.result.message else "liaison établie"
            return f"Imprimante prête : {self.chosen.label} ({first})"
        if self.result and not self.result.ok:
            first = self.result.message.splitlines()[0] if self.result.message else "pas de réponse"
            return f"Imprimante trouvée mais muette : {self.chosen.label} ({first})"
        return f"Imprimante trouvée : {self.chosen.label}"


def detect_printer(progress: ProgressCb | None = None, message: Callable[[str], None] | None = None,
                   language: str = "tspl", discover: bool = True) -> Detection:
    """Cherche l'imprimante, retient la plus probable et vérifie la liaison.

    Conçu pour tourner dans un thread (voir `ui.workers.Worker`) : le scan Bluetooth et le test de
    connexion peuvent prendre plusieurs secondes.
    """
    report = progress or (lambda _pct: None)
    say = message or (lambda _text: None)
    say("Recherche de l'imprimante…")
    devices = scan_devices(discover)
    report(30)
    chosen = next((d for d in devices if d.usable), None)
    if chosen is None:
        report(100)
        return Detection(devices)
    say(f"Liaison avec {chosen.label}…")
    report(50)
    result = test_connection(chosen.config, language)
    report(100)
    return Detection(devices, chosen, result)


# ================================================================== appairage Bluetooth
@dataclass
class PairResult:
    ok: bool
    message: str


def _bluetoothctl(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Lance bluetoothctl avec un agent qui répond 0000 aux demandes de code PIN."""
    return subprocess.run(
        ["bluetoothctl", "--agent", "NoInputNoOutput", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _pair_error(low: str, raw: str, label: str, pin: str = "0000") -> str:
    """Message d'erreur lisible à partir de la sortie de bluetoothctl."""
    if "authentication" in low or "pin" in low:
        return (f"Appairage refusé pour {label} : code PIN incorrect ou demande annulée. "
                f"L'application a envoyé {pin} ; essayez le code demandé par l'imprimante "
                "(0000 par défaut, parfois 1234), ou appairez-la une fois avec l'outil Bluetooth "
                "de Linux Mint.")
    if "not available" in low or "does not exist" in low or "doesnotexist" in low:
        return (f"{label} n'est plus visible : relancez la recherche, imprimante allumée et en mode appairage.")
    if "in progress" in low:
        return f"Appairage déjà en cours pour {label} : patientez quelques secondes, puis réessayez."
    detail = (raw.strip().splitlines() or ["erreur inconnue"])[-1]
    return f"Échec de l'appairage de {label} : {detail[:120]}"


def _pair_with_pin(mac: str, pin: str, timeout: float) -> str:
    """Pilote `bluetoothctl` en interactif pour saisir le code PIN demandé par l'imprimante.

    L'agent « NoInputNoOutput » répond toujours 0000 ; pour un autre code, il faut un agent qui
    saisit réellement le PIN, donc une session interactive dont on lit les invites. Renvoie la
    sortie complète, analysée comme celle de `bluetoothctl pair`.
    """
    proc = subprocess.Popen(
        ["bluetoothctl", "--agent", "KeyboardDisplay"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output: list[str] = []

    def send(text: str) -> None:
        proc.stdin.write(text)
        proc.stdin.flush()

    try:
        send(f"pair {mac}\n")
        deadline = time.monotonic() + timeout
        answered = False
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            output.append(line)
            low = line.lower()
            try:
                if ("pin" in low or "passkey" in low) and "?" not in low and not answered:
                    send(f"{pin}\n")
                    answered = True
                elif "confirm" in low and ("yes" in low or "no" in low):
                    send("yes\n")  # passkey affiché par l'appareil : on l'accepte
            except OSError:  # bluetoothctl s'est arrêté entre-temps
                break
            if "pairing successful" in low or "failed" in low:
                break
        else:
            raise subprocess.TimeoutExpired("bluetoothctl", timeout)
    finally:
        try:
            send("quit\n")
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    return "".join(output)


def _ensure_known(mac: str, timeout: float) -> None:
    """Fait réapparaître l'appareil dans BlueZ s'il en a disparu.

    BlueZ retire les appareils non appairés quand le scan se termine : un appareil choisi dans la
    liste quelques secondes plus tôt répond alors « not available » à l'appairage. Un court scan
    avant `pair` suffit à le réinscrire.
    """
    try:
        known = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True,
                               timeout=5, check=False).stdout
        if any(found == mac for found, _name in _parse_devices(known)):
            return
        subprocess.run(["bluetoothctl", "--timeout", "4", "scan", "on"], capture_output=True,
                       text=True, timeout=min(timeout, 12), check=False)
    except (OSError, subprocess.SubprocessError):
        return


def pair_bluetooth(mac: str, name: str = "", timeout: float = 30.0, pin: str = "0000") -> PairResult:
    """Appaire puis approuve (`trust`) une imprimante Bluetooth, sans passer par le terminal.

    Avec le code par défaut (0000), BlueZ est piloté par l'agent « NoInputNoOutput », qui répond
    0000 aux demandes de l'imprimante — le cas des Phomemo. Un autre code est saisi à la main par
    un agent interactif, BlueZ n'acceptant pas de PIN imposé autrement. Le résultat porte un
    message directement affichable dans l'interface.
    """
    mac = (mac or "").strip().upper()
    if not MAC_RE.match(mac):
        return PairResult(False, "Adresse MAC invalide (format attendu : AA:BB:CC:DD:EE:FF).")
    pin = (pin or "0000").strip()
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        return PairResult(False, "Code PIN invalide : 4 à 6 chiffres (0000 par défaut).")
    label = name or mac
    _ensure_known(mac, timeout)
    try:
        if pin == "0000":
            paired = _bluetoothctl(["pair", mac], timeout)
            raw = f"{paired.stdout}\n{paired.stderr}"
        else:
            raw = _pair_with_pin(mac, pin, timeout)
    except FileNotFoundError:
        return PairResult(False, "bluetoothctl est introuvable : installez BlueZ (sudo apt install bluez).")
    except subprocess.TimeoutExpired:
        return PairResult(False, f"Délai dépassé pour {label} : allumez l'imprimante, mettez-la en mode "
                                 "appairage, puis réessayez.")
    low = raw.lower()
    if "pairing successful" not in low and "alreadyexists" not in low:
        return PairResult(False, _pair_error(low, raw, label, pin))

    try:
        trusted = _bluetoothctl(["trust", mac], timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return PairResult(True, f"{label} est appairée ; l'approbation (trust) a échoué, "
                                "la reconnexion automatique peut ne pas fonctionner.")
    if "succeeded" not in f"{trusted.stdout}{trusted.stderr}".lower():
        return PairResult(True, f"{label} est appairée, mais l'approbation (trust) n'a pas été confirmée.")
    return PairResult(True, f"{label} est appairée et approuvée.")


@dataclass
class ForgetResult:
    ok: bool
    message: str


def forget_bluetooth(mac: str, name: str = "", timeout: float = 15.0) -> ForgetResult:
    """Retire (oublie) un appareil appairé de BlueZ, sans passer par le terminal.

    L'appareil disparaît des « appareils appairés » : il faudra l'appairer de nouveau pour
    l'utiliser. Un appareil déjà absent est annoncé comme oublié, pas comme une erreur.
    """
    mac = (mac or "").strip().upper()
    if not MAC_RE.match(mac):
        return ForgetResult(False, "Adresse MAC invalide (format attendu : AA:BB:CC:DD:EE:FF).")
    label = name or mac
    try:
        removed = _bluetoothctl(["remove", mac], timeout)
    except FileNotFoundError:
        return ForgetResult(False, "bluetoothctl est introuvable : installez BlueZ (sudo apt install bluez).")
    except subprocess.TimeoutExpired:
        return ForgetResult(False, f"Délai dépassé : {label} n'a pas été oublié. "
                                   "Vérifiez que le Bluetooth est actif, puis réessayez.")
    raw = f"{removed.stdout}\n{removed.stderr}"
    low = raw.lower()
    if "no default controller" in low or "not ready" in low:
        return ForgetResult(False, "Adaptateur Bluetooth indisponible : activez le Bluetooth de la machine, "
                                   "puis réessayez.")
    if "removed" in low or "success" in low:
        return ForgetResult(True, f"{label} a été oublié : il n'est plus appairé.")
    if "not available" in low or "does not exist" in low or "doesnotexist" in low or "not found" in low:
        return ForgetResult(True, f"{label} n'était plus appairé, rien à retirer.")
    detail = (raw.strip().splitlines() or ["erreur inconnue"])[-1]
    return ForgetResult(False, f"Échec de l'oubli de {label} : {detail[:120]}")
