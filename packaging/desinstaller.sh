#!/usr/bin/env bash
# Désinstalle Open Thermal Printer installé depuis l'archive autonome.
#
# Retire l'entrée de menu et l'icône, puis demande s'il faut aussi supprimer le
# dossier de l'application et les réglages. À lancer depuis le dossier extrait :
#   ./desinstaller.sh        (pose les questions)
#   ./desinstaller.sh -y     (supprime tout, sans confirmation)
#
# Pour une installation par paquet Debian : sudo apt remove open-thermal-printer.
set -euo pipefail
# Chemin absolu retenu avant le cd : « --help » relit ce fichier, et un chemin relatif
# ne se résoudrait plus une fois le répertoire courant changé.
script=$(readlink -f "$0")
cd "$(dirname "$script")"
here=$(pwd)

data=${XDG_DATA_HOME:-$HOME/.local/share}
config=${XDG_CONFIG_HOME:-$HOME/.config}/OpenThermalPrinter
state=${XDG_STATE_HOME:-$HOME/.local/state}/open-thermal-printer
apps=$data/applications
desktop=$apps/open-thermal-printer.desktop
icon=$data/icons/hicolor/256x256/apps/open-thermal-printer.png

assume_yes=0
case "${1:-}" in
    -y|--oui) assume_yes=1 ;;
    # En-tête du fichier, sans dépendre d'un numéro de ligne : tout le bloc de commentaires
    # initial, jusqu'à la première ligne qui n'en est pas un.
    -h|--help) awk 'NR > 1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$script"; exit 0 ;;
    "") ;;
    *) echo "Option inconnue : $1 (essayez --help)" >&2; exit 2 ;;
esac

ask() {
    if [ "$assume_yes" -eq 1 ]; then
        return 0
    fi
    printf '%s [o/N] ' "$1"
    local answer=""
    read -r answer || return 1  # entrée fermée : on s'arrête sans rien supprimer de plus
    case "$answer" in
        [oOyY]*) return 0 ;;
        *) return 1 ;;
    esac
}

# Une installation système n'a pas à être démontée à la main : apt s'en charge, et
# supprimer /opt à la main laisserait le paquet dpkg dans un état incohérent.
if [ "$here" = "/opt/open-thermal-printer" ]; then
    echo "Installation système détectée : utilisez « sudo apt remove open-thermal-printer »." >&2
    exit 1
fi
# Garde-fou : on ne supprime un dossier que s'il contient vraiment l'application. Le seul
# lanceur ne suffit pas à l'identifier : le dossier packaging/ de ce dépôt en contient un aussi.
if [ ! -d "$here/OpenThermalPrinter" ]; then
    echo "$here ne ressemble pas au dossier d'Open Thermal Printer : rien n'a été supprimé." >&2
    exit 1
fi

echo "Désinstallation d'Open Thermal Printer"
echo "  dossier : $here"

removed=0
for path in "$desktop" "$icon"; do
    if [ -e "$path" ]; then
        rm -f "$path"
        echo "✔ Retiré : $path"
        removed=$((removed + 1))
    fi
done
if [ "$removed" -eq 0 ]; then
    echo "  Aucune entrée de menu ni icône à retirer (déjà désinstallé ?)."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$apps" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true
fi

delete_app=0
if ask "Supprimer aussi le dossier de l'application ($here) ?"; then
    delete_app=1
fi

delete_settings=0
if ask "Supprimer aussi vos réglages (profils, connexion, PIN) et le journal d'erreurs ?"; then
    delete_settings=1
fi

if [ "$delete_settings" -eq 1 ]; then
    for path in "$config" "$state"; do
        if [ -e "$path" ]; then
            rm -rf "$path"
            echo "✔ Retiré : $path"
        fi
    done
else
    echo "  Réglages conservés : $config"
    echo "                     $state"
fi

if [ "$delete_app" -eq 1 ]; then
    # Le script vit dans ce dossier : on en sort avant de le supprimer.
    cd /
    rm -rf "$here"
    echo "✔ Dossier supprimé : $here"
else
    echo "  Dossier conservé : $here"
fi

echo
echo "Open Thermal Printer est désinstallé."
