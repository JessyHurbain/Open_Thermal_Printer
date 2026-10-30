#!/usr/bin/env bash
# Construit le paquet Linux autonome (aucun Python requis chez l'utilisateur) :
#   dist/OpenThermalPrinter-<version>-linux-x86_64.tar.gz
#
# Le binaire est figé avec PyInstaller : PyQt6, Pillow, pypdfium2 (avec sa bibliothèque PDFium),
# pdfminer.six et le code du projet sont embarqués. L'archive contient en plus un lanceur, une
# entrée de menu, l'icône et un installateur local sans root.
#
# Usage : ./empaqueter.sh          (crée .venv et installe les dépendances au besoin)
set -euo pipefail
cd "$(dirname "$0")"

# Dépendances déjà présentes (intégration continue) : on ne recrée pas d'environnement.
if python3 -c "import PyInstaller, PyQt6" >/dev/null 2>&1; then
    py=python3
elif [ -x .venv/bin/python ] && .venv/bin/python -c "import PyInstaller" >/dev/null 2>&1; then
    py=.venv/bin/python
else
    python3 -m venv .venv
    .venv/bin/pip install --upgrade -q pip
    .venv/bin/pip install -q -r requirements.txt -r requirements-build.txt
    py=.venv/bin/python
fi

version=$("$py" -c "from open_thermal_printer import __version__; print(__version__)")
name=OpenThermalPrinter
stage="open-thermal-printer-$version"

echo "→ Version $version, construction du binaire…"
# Une archive d'une version précédente resterait sinon dans dist/ et finirait en pièce
# jointe de la release (le workflow attache « dist/*.tar.gz »).
rm -rf build dist/"$name" dist/"$stage" dist/*.tar.gz dist/SHA256SUMS.txt
"$py" -m PyInstaller --noconfirm --clean --windowed --name "$name" \
    --icon docs/icons/open-thermal-printer-256.png \
    --collect-binaries pypdfium2 --collect-data pypdfium2 \
    --distpath dist --workpath build --specpath build \
    main.py >/dev/null

echo "→ Assemblage du paquet…"
mkdir -p "dist/$stage/share/icons/hicolor/256x256/apps"
mv "dist/$name" "dist/$stage/"
install -m 755 packaging/open-thermal-printer "dist/$stage/open-thermal-printer"
install -m 755 packaging/installer.sh "dist/$stage/installer.sh"
install -m 644 packaging/open-thermal-printer.desktop.in "dist/$stage/open-thermal-printer.desktop"
install -m 644 LICENSE README.md "dist/$stage/"
install -m 644 docs/icons/open-thermal-printer-256.png \
    "dist/$stage/share/icons/hicolor/256x256/apps/open-thermal-printer.png"

echo "→ Archive…"
tar -C dist -czf "dist/$name-$version-linux-x86_64.tar.gz" "$stage"
rm -rf "dist/$stage" build

echo "✔ dist/$name-$version-linux-x86_64.tar.gz ($(du -h "dist/$name-$version-linux-x86_64.tar.gz" | cut -f1))"
echo "  Extraction puis ./installer.sh pour l'ajouter au menu."
