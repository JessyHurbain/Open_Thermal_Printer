#!/usr/bin/env bash
# Construit le paquet Debian (Ubuntu, Linux Mint) à partir du binaire autonome :
#   dist/open-thermal-printer_<version>-1_amd64.deb
#
# Le contenu est le même que l'archive .tar.gz — binaire PyInstaller, Qt et PDFium
# compris — installé dans /opt/open-thermal-printer, plus une entrée de menu, une
# icône et un lanceur dans /usr/bin. Rien n'est compilé ici : le paquet reprend
# l'archive produite par empaqueter.sh, reconstruite si elle manque.
#
# Usage : ./empaqueter-deb.sh
set -euo pipefail
cd "$(dirname "$0")"

pkg=open-thermal-printer
arch=amd64
version=$(python3 -c "from open_thermal_printer import __version__; print(__version__)")
debversion="$version-1"  # révision Debian : à incrémenter pour republier sans changer d'amont
tarball="dist/OpenThermalPrinter-$version-linux-x86_64.tar.gz"

if [ ! -f "$tarball" ]; then
    echo "→ $tarball absent, construction du paquet autonome…"
    ./empaqueter.sh
fi

work=build/deb
root="$work/${pkg}_${debversion}_${arch}"
rm -rf "$work"
mkdir -p "$work"
tar -xzf "$tarball" -C "$work"
extracted="$work/open-thermal-printer-$version"

echo "→ Assemblage de l'arborescence Debian…"
mkdir -p "$root/DEBIAN" \
    "$root/opt/$pkg" \
    "$root/usr/bin" \
    "$root/usr/share/applications" \
    "$root/usr/share/icons/hicolor/256x256/apps" \
    "$root/usr/share/doc/$pkg"

# Le binaire et ses accompagnements vont dans /opt ; l'installateur local, son désinstalleur
# et l'entrée de menu de l'archive n'ont plus lieu d'être, le paquet s'en charge (apt remove).
cp -a "$extracted/." "$root/opt/$pkg/"
rm -f "$root/opt/$pkg/installer.sh" "$root/opt/$pkg/desinstaller.sh" \
    "$root/opt/$pkg/open-thermal-printer.desktop"
install -m 644 "$extracted/share/icons/hicolor/256x256/apps/open-thermal-printer.png" \
    "$root/usr/share/icons/hicolor/256x256/apps/open-thermal-printer.png"
# L'icône est désormais fournie par /usr/share : la copie de l'archive serait en double.
rm -rf "$extracted/share" "$extracted"

cat > "$root/usr/bin/$pkg" <<'LAUNCHER'
#!/bin/sh
# Lanceur système : l'application vit dans /opt et se résout elle-même.
exec /opt/open-thermal-printer/open-thermal-printer "$@"
LAUNCHER

# Même entrée de menu que l'archive, mais sur le lanceur du PATH : l'installation
# système ne dépend plus du dossier où l'utilisateur a extrait l'archive.
sed 's|@EXEC@|open-thermal-printer|' packaging/open-thermal-printer.desktop.in \
    > "$root/usr/share/applications/$pkg.desktop"

install -m 644 packaging/deb/copyright "$root/usr/share/doc/$pkg/copyright"
install -m 644 LICENSE "$root/usr/share/doc/$pkg/LICENSE"
install -m 644 README.md "$root/usr/share/doc/$pkg/README.md"

# Journal des modifications au format Debian, tiré de CHANGELOG.md, et entrée
# changelog.Debian (le paquet est produit en amont : une seule entrée par version).
# Le format Debian est strict : chaque ligne commence par deux espaces, les titres
# Markdown deviennent des puces, et les lignes de continuation sont réindentées.
{
    printf '%s (%s) stable; urgency=medium\n\n' "$pkg" "$debversion"
    awk -v v="$version" '
        $0 ~ "^## " v "($|[^0-9])" { inside = 1; next }
        inside && /^## / { exit }
        !inside { next }
        /^[[:space:]]*$/ { next }
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            if (line ~ /^### /) { sub(/^### /, "", line); print "  * " line; emitted = 1; next }
            if (line ~ /^- /) { sub(/^- /, "", line); print "    - " line; emitted = 1; next }
            if (!emitted) { print "  * " line; emitted = 1; next }
            print "      " line
        }
    ' CHANGELOG.md
    printf '\n -- %s  %s\n' \
        "Jessy Hurbain <JessyHurbain@users.noreply.github.com>" "$(date -R)"
} | gzip -9n > "$root/usr/share/doc/$pkg/changelog.Debian.gz"
gzip -9n -c CHANGELOG.md > "$root/usr/share/doc/$pkg/changelog.gz"

install -m 755 packaging/deb/postinst "$root/DEBIAN/postinst"
install -m 755 packaging/deb/postrm "$root/DEBIAN/postrm"

installed_size=$(du -sk --exclude=DEBIAN "$root" | cut -f1)
sed -e "s|@VERSION@|$debversion|" -e "s|@ARCH@|$arch|" -e "s|@INSTALLED_SIZE@|$installed_size|" \
    packaging/deb/control.in > "$root/DEBIAN/control"

# Droits : tout est lisible, seuls le binaire, le lanceur et les scripts sont exécutables.
find "$root" -type d -exec chmod 755 {} +
find "$root" -type f -exec chmod 644 {} +
chmod 755 "$root/opt/$pkg/open-thermal-printer" \
    "$root/opt/$pkg/OpenThermalPrinter/OpenThermalPrinter" \
    "$root/usr/bin/$pkg" "$root/DEBIAN/postinst" "$root/DEBIAN/postrm"

echo "→ Construction du .deb…"
deb="dist/${pkg}_${debversion}_${arch}.deb"
rm -f dist/*.deb
dpkg-deb --build --root-owner-group "$root" "$deb" >/dev/null
rm -rf "$work"

echo "✔ $deb ($(du -h "$deb" | cut -f1))"
dpkg-deb --info "$deb" | sed -n '2,8p'
echo "  Installer :  sudo apt install ./$deb"
echo "  Désinstaller : sudo apt remove $pkg"
