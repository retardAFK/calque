# Calque

Décalque une image sur ta feuille en regardant l'écran : la caméra filme, ton image se superpose par transparence.

## Tester

Ouvre la page en HTTPS sur ton téléphone (la caméra ne marche pas en `file://` ni en HTTP).

- **GitHub Pages** : Settings → Pages → Source : branche `main`, dossier `/`. Le lien arrive en une minute.
- **En local avec ton téléphone** : `npx serve` puis un tunnel HTTPS (`npx localtunnel --port 3000`, ou ngrok).

Sur Android/Chrome : menu ⋮ → *Ajouter à l'écran d'accueil* pour l'avoir en plein écran comme une app.

## Ce que ça fait

- Opacité réglable
- 1 doigt : déplacer — 2 doigts : zoom et rotation
- Verrouillage pour ne plus bouger l'image en dessinant
- Rendus : photo, contraste, contours (détection Sobel en local), négatif
- Miroir
- L'écran reste allumé (Wake Lock)

## Limite connue

L'image est fixe par rapport à l'écran, pas à la feuille. Si le téléphone bouge, le dessin bouge avec. Il faut le caler (verre, pile de livres, support). Ancrer l'image sur la feuille demande ARCore, donc une app native.

Aucune image ne quitte le téléphone : tout est traité dans le navigateur.
