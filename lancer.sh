#!/usr/bin/env bash
# Lance l'application ; crée l'environnement virtuel et installe les dépendances au premier démarrage.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python main.py "$@"
