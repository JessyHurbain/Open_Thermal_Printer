#!/usr/bin/env bash
# Lance la suite de tests ; crée l'environnement virtuel et installe les dépendances au premier lancement.
# Usage : ./tester.sh                      (toute la suite)
#         ./tester.sh tests.test_ui        (seulement ces modules)
#         ./tester.sh -p test_ui.py        (seulement les fichiers correspondant au motif)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# Les tests d'interface tournent sans serveur graphique : rien à afficher, aucun écran requis.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

# Un premier argument qui ne commence pas par « - » est un nom de module de test.
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    exec .venv/bin/python -m unittest -v "$@"
fi

exec .venv/bin/python -m unittest discover -s tests -v "$@"
