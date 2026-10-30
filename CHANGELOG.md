# Journal des versions

Les notes de chaque release GitHub sont reprises de la section correspondant à son numéro.
Une nouvelle version se prépare donc ici : ajoutez une section `## x.y.z`, relevez
`__version__` dans `open_thermal_printer/__init__.py`, puis poussez le tag `vx.y.z`.

## 1.0.1 — 30 octobre 2026

### Distribution Linux

- **Paquet Debian** (`open-thermal-printer_1.0.1-1_amd64.deb`) : même binaire autonome que
  l'archive, installé dans `/opt/open-thermal-printer` avec son lanceur, son entrée de menu et son
  icône. `sudo apt install ./open-thermal-printer_*.deb` installe au passage les bibliothèques Qt
  dont il dépend.
- **Désinstalleur** pour l'archive (`./desinstaller.sh`) : retire l'entrée de menu et l'icône, puis
  propose de supprimer le dossier et les réglages ; `-y` fait tout sans poser de question.

### Corrections

- Changer de résolution (203 ↔ 300 dpi) ne rétrécit plus l'étiquette : la taille physique en
  millimètres est conservée, seuls les nombres affichés en points sont recalculés.
- Les flèches avancent de 0,5 mm (2 mm avec Maj) quand l'alignement sur la grille est désactivé,
  au lieu d'appliquer malgré tout le pas de la grille.
- Mode natif : les caractères absents du CP1252 sont signalés avant impression. La police interne
  les remplaçait par des « ? » sans prévenir ; le mode raster, lui, les imprime correctement.
- L'ouverture de la fenêtre n'attend plus `bluetoothctl` : la liste des appareils appairés arrive
  en arrière-plan, et appliquer un profil Bluetooth ne bloque plus l'interface.

## 1.0.0 — 30 octobre 2026

Première version publiée.

### Édition des étiquettes

- Aperçu fidèle au rendu d'impression : les éléments se déplacent à la souris, se règlent au
  clavier (0,5 mm par flèche, 2 mm avec Maj) et se réordonnent par calques.
- Quadrillage millimétré, règles graduées autour de l'aperçu et **guides magnétiques** qui
  alignent sur les bords et le centre des autres éléments, suspendus en maintenant **Alt**.
- Annuler / rétablir sur les déplacements et les modifications d'éléments.
- Éléments : texte (police, corps, gras, italique, alignement, multi-lignes), codes-barres
  (Code 128, Code 39, EAN-13, EAN-8, UPC-A), QR code, images et logos (seuil, tramage Floyd-Steinberg
  ou Bayer, inversion), rectangles, traits. Rotation 0/90/180/270° pour chacun.
- Liste des éléments avec une **miniature du rendu réel** de chaque élément.

### Impression

- **TSPL** (par défaut) ou **ZPL II**, en mode **raster** (rendu identique à l'aperçu, accents
  compris) ou **natif** (trames légères, polices internes de l'imprimante).
- Connexion **Bluetooth RFCOMM**, **USB** (`/dev/usb/lp0`) et **série** (`/dev/ttyUSB0`…), sans CUPS.
- **Détection automatique** de l'imprimante, **appairage et oubli** d'un appareil Bluetooth depuis
  l'application, code PIN d'appairage modifiable, **état de la connexion affiché en permanence**
  dans la barre d'état.
- **Profils de périphériques** : une imprimante avec ses réglages propres (format, résolution,
  noirceur, vitesse), et bascule de l'une à l'autre en un clic.

### Import de bordereaux

- Ouverture d'un bordereau **PDF ou image** (Vinted, Leboncoin, Mondial Relay, Colissimo…) :
  détection et rognage de l'étiquette, rotation et orientation automatiques, ajustement au format
  thermique, seuil noir/blanc pour garder des codes-barres lisibles.
- Préréglages d'import enregistrables.

### Le reste

- Projets `.label.json` (images incluses), export PNG, export `.tspl` / `.zpl`, panneau des
  commandes générées, modèles prêts à l'emploi.
- Journal d'erreurs avec rotation et boîte de dialogue sur exception non gérée.
- 198 tests, dont une partie en Qt hors écran, exécutés par un hook Git et par l'intégration
  continue (Python 3.10 et 3.12, `ruff`, `mypy`).
- Licence **MIT**, dépendances toutes permissives.
