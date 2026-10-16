# Notes de travail

## Règles du projet

- Le bouton « Choisir une image » est un `<label for="file">` : le sélecteur s'ouvre au tap, sans JS.
- La caméra n'est demandée qu'après le choix de l'image.
- `sw.js` : réseau d'abord, cache seulement hors ligne. À chaque changement de fichier servi :
  incrémenter `calque-vN` et tenir `FILES` à jour (le test vérifie que tout y est).
- Le modèle est centré par `left:50%; top:50%; transform: translate(-50%, -50%) …`.
- Rien ne quitte le téléphone : pas de CDN, polices et bibliothèques copiées dans le dépôt.
- Tests : `tests/test_calque.py` (Playwright). Zéro erreur console / pageerror sur toutes les pages,
  trois tailles (390×844, 844×390, 1024×1366). Code de sortie ≠ 0 au moindre échec.

## Verrou ancré (`js/anchor.js`)

- Image de travail : vidéo réduite à 320 px de large, niveaux de gris.
- Au verrouillage : coins yape06 (jsfeat) sur l'image lissée, 120 max, répartis sur une grille 8×8 ;
  patchs 11×11 sur 3 niveaux de pyramide, gradients et hessienne précalculés ; descripteurs ORB.
- À chaque frame (`requestVideoFrameCallback`, sinon `requestAnimationFrame`) : Lucas-Kanade
  inverse compositionnel écrit à la main — chaque patch est relu dans l'image courante *à travers
  l'estimation courante M* (rotation et échelle comprises). Le résidu est estimé par RANSAC
  (similitude à 2 points, seuil 1,5 px) puis composé avec M. Comme on compare toujours à la
  référence, il n'y a pas de dérive : c'est le « recalage régulier », fait à chaque frame.
- Patchs centrés (moyenne retirée) : insensibles aux changements d'exposition quand la main entre.
  Un patch dont l'écart moyen dépasse 20 niveaux de gris est écarté avant RANSAC.
- Perdu si moins de max(12, 25 % des points) inliers : M figé, « SUIVI PERDU ». Chaque frame, on
  retente le suivi depuis M figé ; une frame sur trois, recherche globale ORB (Hamming + test de
  ratio + RANSAC) pour retrouver la feuille où qu'elle revienne.
- Passage vidéo → écran : échelle de cover `s = max(W/vw, H/vh)`, décalage du recadrage, puis la
  similitude écran est composée avec la transformation utilisateur (centre, rotation, échelle).
- Déverrouiller : la position affichée est reportée dans la transformation utilisateur (pas de
  saut), le suivi s'arrête, les gestes reviennent. Changement de format de la caméra : idem puis
  nouvelle référence.
- jsfeat 0.0.8 (MIT) dans `js/vendor/`. Son `optical_flow_lk` recalculait les dérivées de la
  référence à chaque frame (~3,7 ms) : remplacé par le LK maison (~1,2–1,6 ms).
- Budget : moyenne glissante du temps par frame ; au-delà de 10 ms on saute des frames.

### Temps mesurés (PC de test, Chromium headless, vidéo 360×640)

| scénario | moyenne | max |
|---|---|---|
| décalage 30 px (3 tailles) | 3,9–4,1 ms | 16 ms |
| rotation 5° | 4,6 ms | 15 ms |
| main qui passe | 3,9 ms | 10 ms |
| sortie / retour | 4,7 ms | 17 ms |

Répartition : lecture de la frame (drawImage + getImageData) ~1,9 ms, niveaux de gris 0,45,
pyramide 0,3, LK 1,1–1,6, RANSAC < 0,1. Capture de référence au verrouillage : ~50 ms (une fois,
surtout l'allocation du tampon de coins de yape06).

Précision sur vidéos factices : décalage à 0,05 px écran près, rotation 5,00°, main : 0,02 px.

### Limites, à vérifier sur téléphone

- Similitude seulement : une forte inclinaison du téléphone (perspective) n'est pas modélisée.
- Les vidéos de test sont propres (pas de flou de bougé, d'autofocus, de reflets).
- Un téléphone est 2 à 4× plus lent que le PC de test : attendu 8–16 ms par frame traitée,
  le saut de frames prend le relais au-delà de 10 ms.
