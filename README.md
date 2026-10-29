# Phomemo Label Studio

Application de bureau (Python + PyQt6) pour concevoir et imprimer des étiquettes thermiques
sur une **Phomemo PM-241-BT** sous **Linux Mint**, en TSPL (ou ZPL), **sans CUPS**.

## Fonctions

- **Connexion** : Bluetooth RFCOMM (sockets standard, adresse MAC), USB (`/dev/usb/lp0`), port série (`/dev/ttyUSB0`, `/dev/rfcomm0`…) ; **détection automatique de l'imprimante** (Bluetooth appairé, USB, série), **appairage et oubli d'un appareil Bluetooth depuis l'application**, bouton *Tester la connexion* (statut + modèle) et *Auto-test* (SELFTEST).
- **Éditeur visuel** : aperçu fidèle, éléments déplaçables à la souris (flèches du clavier : 0,5 mm, avec Maj : 2 mm), ordre des calques, duplication, zoom (Ctrl + molette).
- **Éléments** : texte (police, taille, gras/italique, alignement, multi-lignes), code-barres (Code 128, Code 39, EAN-13, EAN-8, UPC-A), QR code (4 niveaux de correction), images/logos (seuil, Floyd-Steinberg ou tramage de Bayer, inversion), rectangles et traits. Rotation 0/90/180/270° pour chacun.
- **Format** : formats prédéfinis (100×150 mm, 50×30 mm…) ou libre, en **mm ou en points**, 203/300 dpi, gap / repère noir / continu, noirceur, vitesse, copies.
- **Langages** : TSPL (par défaut) ou ZPL II, en deux modes :
  - **Raster** (recommandé) : l'étiquette est envoyée en une image `BITMAP` → rendu identique à l'aperçu, accents et polices TrueType compris.
  - **Natif** : `TEXT` / `BARCODE` / `QRCODE` / `BOX` (ZPL : `^A0` / `^B*` / `^BQ` / `^GB`) — très léger, mais limité aux polices internes de l'imprimante.
- **Import d'étiquettes de transport (Vinted, Leboncoin…)** : ouvre un bordereau PDF ou image, détecte et rogne l'étiquette, l'ajuste au format thermique (rotation automatique, marges, seuil noir/blanc pour garder des codes-barres lisibles) et l'imprime en un clic.
- Panneau « Commandes générées » (ce qui sera réellement envoyé), export `.tspl` / `.zpl`, export PNG, projets `.label.json` (images incluses), modèles prêts à l'emploi.

## Installation (Linux Mint 21/22)

```bash
sudo apt update
sudo apt install python3-venv python3-pip bluez libxcb-cursor0 libxkbcommon-x11-0 fonts-dejavu-core fontconfig

cd phomemo_label_studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> `libxcb-cursor0` est indispensable à Qt ≥ 6.5 sous X11 (sinon : « Could not load the Qt platform plugin "xcb" »).
> Utilisez le Python **du système** (`/usr/bin/python3`) : c'est lui qui est compilé avec le support Bluetooth (`socket.AF_BLUETOOTH`). Un Python installé via pyenv/conda peut ne pas l'avoir.
> Pas besoin de PyBluez : il n'est plus maintenu et ne s'installe plus avec les Python récents.

Alternative sans pip pour PyQt : `sudo apt install python3-pyqt6` puis `python3 -m venv --system-site-packages .venv`.

## Lancer

```bash
./lancer.sh            # crée le venv au premier lancement, puis démarre l'application
# ou
.venv/bin/python main.py
```

## Tester

```bash
./tester.sh                 # crée le venv au besoin, installe les dépendances et lance les 91 tests
./tester.sh -p test_ui.py   # seulement les fichiers correspondant au motif
```

Les tests d'interface tournent en mode Qt `offscreen` : aucun serveur graphique, aucune imprimante et aucune modification de votre configuration ne sont nécessaires. Le script renvoie le code de sortie de la suite (0 si tout passe), donc il s'enchaîne dans un hook ou une intégration continue.

## Imprimer des étiquettes Vinted / Leboncoin

1. Téléchargez vos bordereaux (PDF) depuis Vinted ou Leboncoin.
2. Dans l'application : **Fichier → Importer des étiquettes Vinted / Leboncoin…** (Ctrl+I), ou **glissez un ou plusieurs PDF sur la fenêtre**. Chaque page de chaque PDF devient une entrée de la file d'attente.
3. Pour chaque entrée, l'étiquette est repérée automatiquement (cadre bleu). Sinon : cliquez sur un bloc orange, ou tracez un rectangle à la souris ; *Retirer les marges* resserre le cadre ; *↻ 90°* tourne.
4. Choisissez le format de votre rouleau (100 × 150 mm par exemple) ; l'aperçu en bas montre exactement ce qui sortira.
5. **Importer et imprimer** : avec une seule entrée, elle part directement ; avec plusieurs, **« Imprimer les N étiquettes »** les envoie à la suite sur une seule connexion Bluetooth, après confirmation. *Remplacer* / *Ajouter* chargent l'étiquette sélectionnée dans l'éditeur pour la retoucher (ajouter un texte, un n° de commande…).

**Reconnaissance du transporteur.** L'application lit le texte du PDF (Mondial Relay, Colissimo, Chronopost, Relais Colis, Vinted Go, DPD, GLS, UPS, DHL…, et Vinted / Leboncoin). Cliquez une fois sur **Mémoriser…** après avoir ajusté un rognage : il sera ensuite **appliqué automatiquement** à tous les PDF du même transporteur et du même format de page. Le message sous la liste dit ce qui a été reconnu et appliqué. *À toute la file* force un préréglage sur toutes les entrées. Les images (PNG, JPG) n'ont pas de texte : elles sont rognées automatiquement mais sans reconnaissance.

**Netteté des codes-barres.** Pour un PDF, la zone rognée est rendue *directement* à la résolution de l'imprimante (pas de réduction après coup). Avec « Optimiser pour les codes-barres » (activé par défaut), l'échelle est ajustée pour que la barre la plus fine tombe sur un nombre entier de points (par exemple 3 pt) : au maximum +3 % dans les marges ou −22 %. Si aucun calage ne convient, l'étiquette est laissée à son échelle naturelle ; le message sous la case l'indique. Si un code semble trop clair ou trop gras, réglez *Noirceur*. Scannez toujours le code du premier tirage avant de coller.

## Connecter l'imprimante

**Détection automatique.** Onglet **Connexion** → **🔍 Détecter l'imprimante** (ou menu *Imprimante*, Ctrl+D) : l'application cherche les appareils utilisables, retient le plus probable — une Phomemo reconnue à son nom, sinon un port USB, puis un port série ou `rfcomm` — remplit la connexion et **teste aussitôt la liaison**. Si une connexion valide est déjà configurée et que le candidat diffère, l'application **demande avant de la remplacer** (la connexion actuelle est proposée par défaut) ; refuser conserve la configuration en place, et une imprimante déjà constatée injoignable est remplacée sans question. Un appareil Bluetooth appairé qui n'est pas une imprimante (casque, enceinte…) est listé mais jamais retenu : mieux vaut aucun choix qu'un mauvais. Si l'imprimante est allumée mais pas encore appairée, un scan Bluetooth borné (quelques secondes) la signale : appairez-la avec *Appairer…*, puis relancez la détection.

**Appairage.** Onglet **Connexion** → *Appairer…* : l'application cherche les appareils à portée, met en avant celui qui ressemble à une imprimante, l'appaire puis l'approuve (*trust*) via BlueZ. Le code envoyé est celui du champ **Code PIN** (**0000** par défaut, parfois **1234** ; la dernière valeur est mémorisée) : avec 0000, BlueZ répond seul à l'imprimante ; avec un autre code, l'application le saisit quand l'imprimante le demande. Aucun terminal n'est nécessaire ; si l'appairage est refusé, le message rappelle le code envoyé, et l'appairage manuel ci-dessous reste possible.

**Oublier un appareil.** Toujours dans l'onglet **Connexion**, sélectionnez l'appareil appairé puis *Oublier* : après confirmation, il est retiré des appareils appairés de BlueZ, disparaît de la liste et la connexion Bluetooth est vidée (l'état repasse à *non configurée*). Utile après un changement de code PIN ou de modèle ; il faudra l'appairer de nouveau pour l'utiliser.

**État en continu.** La barre d'état affiche en permanence l'état de la liaison — **● Imprimante : joignable**, **injoignable** ou **non configurée** (le survol donne le détail de la dernière réponse). Elle est vérifiée toutes les minutes en arrière-plan, jamais pendant une impression, et remise à jour après chaque test ou envoi réussi.

### Bluetooth (RFCOMM)

1. Allumez l'imprimante, puis appairez-la **une fois** — avec le bouton *Appairer…* de l'application (plus haut) ou en ligne de commande :
   ```bash
   bluetoothctl
   power on
   scan on            # repérez « PM-241-BT » et son adresse AA:BB:CC:DD:EE:FF
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   exit
   ```
   (Code PIN éventuel : `0000` ou `1234`.)
2. Dans l'application → onglet **Connexion** → *Bluetooth* : l'appareil apparaît dans « Appareils appairés » (sinon saisissez la MAC), canal RFCOMM **1** par défaut.
3. Fermez l'application mobile Phomemo et déconnectez le téléphone : l'imprimante n'accepte qu'un client à la fois.
4. Cliquez sur **Tester la connexion**.

Si la connexion est refusée alors que l'imprimante est allumée et appairée, essayez un autre canal (1 à 5) ; `sdptool browse AA:BB:CC:DD:EE:FF` (si disponible) liste le canal du service « Serial Port ». Variante équivalente : `sudo rfcomm bind 0 AA:BB:CC:DD:EE:FF 1` puis mode *Port série* sur `/dev/rfcomm0`.

### USB

Branchez le câble ; le périphérique apparaît en `/dev/usb/lp0` (ou `/dev/ttyUSB0` / `/dev/ttyACM0` selon le mode de l'imprimante). Droits d'accès :

```bash
sudo usermod -aG lp,dialout $USER    # puis fermer/rouvrir la session
```

## Utilisation rapide

1. Menu **Modèles** (bordereau 100×150, produit 50×30…) ou **Nouveau**, puis onglet **Étiquette** pour régler format, support (gap), noirceur.
2. Ajoutez des éléments avec les boutons **＋** à gauche ; sélectionnez-les (clic sur l'aperçu ou dans la liste) pour modifier leurs propriétés à droite.
3. Vérifiez le gap et le format correspondent à vos rouleaux, puis **🖨 Imprimer** (Ctrl+P).

## Structure du projet

```
main.py                     point d'entrée
lancer.sh                   lance l'appli (crée le venv au besoin)
requirements.txt
labelstudio/
  models.py                 Element / LabelSettings / Label (+ JSON)
  fonts.py                  résolution des polices TrueType (fontconfig)
  imaging.py                N&B, tramage, rotation, encodage binaire des bitmaps
  renderer.py               éléments -> bitmaps (aperçu ET impression raster) + géométrie native
  tspl.py / zpl.py          générateurs de trames
  printer.py                connexions Bluetooth RFCOMM / USB / série, test de statut
  importer.py               PDF/image -> blocs, rognage, rendu net 203 dpi, calage des barres, transporteur, préréglages
  templates.py              modèles d'étiquettes
  ui/                       canvas, panneaux Étiquette / Élément / Connexion, fenêtre principale
tests/                      91 tests : cœur, import, et interface hors écran (./tester.sh)
```

## Dépannage

| Symptôme | Piste |
|---|---|
| `Connexion refusée` (Bluetooth) | Téléphone encore connecté, mauvais canal, imprimante éteinte |
| `Permission refusée` (USB/série) | `sudo usermod -aG lp,dialout $USER` puis nouvelle session |
| Étiquettes décalées / une étiquette blanche en plus | Lancer la calibration du rouleau sur l'imprimante ; vérifier *Support* et *Espacement* |
| Impression trop claire / trop foncée | Onglet Étiquette → *Noirceur* |
| Image inversée / tête en bas | Onglet Étiquette → *DIRECTION* 0 ↔ 1 |
| Accents manquants en mode natif | Passer en mode **Raster** (polices internes limitées à CP1252) |
| Le test ne renvoie pas de statut | Normal sur certains firmwares : la liaison est établie, l'impression fonctionne quand même |

## Limites connues

- Écrit et testé sans imprimante physique : les trames et l'interface sont vérifiées (tests + rendu), mais le comportement exact de votre PM-241-BT (canal RFCOMM, DIRECTION, réponse au statut) est à confirmer sur le matériel.
- Si votre exemplaire n'expose pas de service Bluetooth « série » classique (certains modèles Phomemo sont BLE uniquement), utilisez l'USB.
- En mode natif, les rotations 90/180/270° et le ZPL natif reposent sur les conventions TSC/Zebra : en cas de décalage, le mode raster est exact.
