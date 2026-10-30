#!/usr/bin/env bash
# Installe l'entrée de menu et l'icône dans le profil de l'utilisateur, sans root.
# L'application reste dans son dossier : déplacez le paquet entier, puis relancez ce script.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
apps="$data/applications"
icons="$data/icons/hicolor/256x256/apps"

chmod +x "$here/open-thermal-printer"
mkdir -p "$apps" "$icons"
cp "$here/share/icons/hicolor/256x256/apps/open-thermal-printer.png" "$icons/"
sed "s|@EXEC@|$here/open-thermal-printer|" "$here/open-thermal-printer.desktop" > "$apps/open-thermal-printer.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true
fi

echo "✔ Open Thermal Printer ajouté au menu (entrée : $apps/open-thermal-printer.desktop)"
echo "  Lanceur : $here/open-thermal-printer"
echo "  Désinstaller : ./desinstaller.sh, dans ce dossier."
