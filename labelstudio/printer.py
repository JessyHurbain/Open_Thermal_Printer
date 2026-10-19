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
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.mac, self.channel))
        except OSError as exc:
            sock.close()
            raise PrinterError(self._explain(exc)) from exc
        self.sock = sock

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


def make_connection(cfg: dict) -> Connection:
    kind = cfg.get("type")
    if kind == "bluetooth":
        return BluetoothConnection(cfg.get("mac", ""), cfg.get("channel", 1))
    if kind == "usb":
        return UsbLpConnection(cfg.get("path") or "/dev/usb/lp0")
    if kind == "serial":
        return SerialConnection(cfg.get("path") or "/dev/ttyUSB0", cfg.get("baudrate", 115200))
    raise PrinterError(f"Type de connexion inconnu : {kind!r}")


# ================================================================== découverte
def list_paired_devices() -> list[tuple[str, str]]:
    """Appareils Bluetooth connus de BlueZ : liste de (adresse MAC, nom)."""
    for cmd in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "paired-devices"], ["bluetoothctl", "devices"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        found = re.findall(r"^Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+(.*)$", out, re.M)
        if found:
            return [(mac.upper(), name.strip()) for mac, name in found]
    return []


def list_serial_ports() -> list[str]:
    patterns = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/rfcomm*")
    return sorted(p for pat in patterns for p in glob.glob(pat))


def list_usb_devices() -> list[str]:
    return sorted(glob.glob("/dev/usb/lp*"))


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


def test_connection(cfg: dict, language: str = "tspl") -> TestResult:
    """Ouvre la liaison puis interroge l'imprimante (statut TSPL ou ZPL)."""
    try:
        with make_connection(cfg) as conn:
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
                lines.append("Aucune réponse au statut (<ESC>!?) — la liaison fonctionne mais ce modèle ne répond peut-être pas.")
                ok = True
            conn._write(b"~!T\r\n")  # nom du modèle
            model = conn.read(64, 1.0).decode("ascii", "replace").strip()
            if model:
                lines.append(f"Modèle : {model}")
            return TestResult(ok, "\n".join(lines))
    except PrinterError as exc:
        return TestResult(False, str(exc))


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
