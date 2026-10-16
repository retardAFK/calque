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
- Verrou ancré : une fois verrouillée, l'image reste collée à la feuille même si le téléphone bouge (suivi de points dans la vidéo, en local). État affiché : *Ancré* ou *Suivi perdu*
- Rendus : photo, contraste, contours en noir (détection Sobel en local), négatif
- Miroir
- L'écran reste allumé (Wake Lock)

## Limites connues

Le verrou ancré suit la feuille en translation, rotation et échelle (similitude), pas en perspective : si le téléphone s'incline beaucoup par rapport à la feuille, le calque glisse un peu. Il faut que la feuille ait quelques traits ou motifs à suivre ; une feuille blanche vide donne *Suivi perdu*. Caler le téléphone reste le plus précis.

Aucune image ne quitte le téléphone : tout est traité dans le navigateur.
