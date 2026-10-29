#!/usr/bin/env bash
# Régénère les captures du README (docs/screenshots) depuis l'application ; crée le venv au besoin.
# Usage : ./captures.sh              (les quatre images)
#         ./captures.sh import       (une seule : editeur, element, connexion, import)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# Aucun écran requis : Qt rend les fenêtres hors écran, la configuration utilisateur est laissée
# intacte et l'imprimante est simulée.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

exec .venv/bin/python outils/captures.py "$@"
