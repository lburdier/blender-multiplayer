# Blender Multiplayer

Éditez **la même scène Blender à plusieurs, en temps réel**. Chacun travaille de son côté, voit les autres se déplacer dans la scène, voit ce qu'ils sélectionnent, créent et modifient, avec un historique collaboratif (Ctrl+Z partagé, retour à n'importe quel point), des autosauvegardes et des sauvegardes restaurables.

Testé sous **Blender 4.3, 5.0 et 5.1**, y compris en mélangeant les versions (hôte 5.1 et client 5.0, et l'inverse). Les API qui changent d'une version à l'autre sont détectées à l'exécution, pour viser aussi les versions suivantes (5.2, 5.3…).

## Installation

1. Télécharger le fichier `blender_multiplayer-x.y.z.zip` de la [dernière version](https://github.com/lburdier/blender-multiplayer/releases/latest) (ne pas le décompresser).
2. Blender > *Edit > Preferences > Get Extensions* > menu ⌄ > **Install from Disk…** > choisir le `.zip`.
3. Dans les préférences de l'extension : choisir son **pseudo** et sa **couleur**.

Tous les joueurs doivent installer la même version de l'addon.

L'interface se trouve dans la vue 3D, barre latérale (**N**) > onglet **Multijoueur**.

## Jouer

| Rôle | Étapes |
|---|---|
| **Hôte** | Ouvrir sa scène > *Héberger* (nom, port, mot de passe facultatif) > communiquer l'adresse affichée (bouton copier) |
| **Joueur** | Saisir l'adresse et le port > *Rejoindre*. La scène de l'hôte remplace la vôtre (votre fichier est sauvegardé avant) |
| **Spectateur** | Cocher *Spectateur* avant de rejoindre : on voit tout, on ne peut rien modifier |

- **Réseau local** : adresse IP de l'hôte (ex. `192.168.1.20`), port `5555` par défaut.
- **Par Internet** : le plus simple est un VPN de jeu (Tailscale, ZeroTier, Radmin VPN…) ; sinon, ouvrir/rediriger le port TCP sur la box de l'hôte. Le pare-feu Windows demandera l'autorisation au premier lancement.

## Ce qui est synchronisé

- Objets : création, suppression, transformations, parents, visibilité, modificateurs (Geometry Nodes avec leurs entrées compris), contraintes, emplacements de matériaux, groupes de vertex, pose des armatures, propriétés personnalisées.
- Maillages : géométrie complète et attributs (UV, couleurs, arêtes vives, index de matériau…), **en direct pendant le mode Édition**, avec un aller-retour bit à bit identique.
- Matériaux, arbres de nodes, mondes, images (le fichier image est transmis), animations (actions 5.x à slots), armatures (os ajoutés en mode Édition), courbes, textes, Grease Pencil, lattices, metaballs, volumes, cheveux, nuages de points, sons, polices… : envoyés en « blob » (data-block complet), donc fidèles à 100 %.
- Lumières, caméras (dont la mise au point), collections, scènes (réglages de rendu, unités, gestion des couleurs, exclusion de calques).

Restent **propres à chaque joueur** : sélection, point de vue, mode, frame courante (partageable par l'hôte avec *Frame partagée*), réglages d'outils.

## Voir les autres

- Chaque joueur apparaît comme une **caméra colorée** à son point de vue, avec son nom, son mode et une ligne de visée.
- Ses objets **sélectionnés** sont encadrés à sa couleur, son **curseur 3D** est affiché.
- Les objets en cours d'édition par quelqu'un (mode Édition, Sculpt, Pose, Peinture) sont **verrouillés** : épais contour + « Verrouillé : Nom ». Personne d'autre ne peut les modifier tant qu'ils sont en cours d'édition.
- **Suivre** (icône œil) : votre vue suit celle d'un joueur ; bougez la vue pour arrêter. **Aller à** (icône caméra) : se placer une fois à son point de vue.
- **Signaler le curseur** : un repère animé apparaît chez tout le monde. **Chat** intégré, messages affichés dans le viewport.

## Historique, Ctrl+Z, sauvegardes

| Action | Effet |
|---|---|
| **Ctrl+Z / Ctrl+Maj+Z** (mode Objet) | Annule / rétablit **votre** dernière action pour tout le monde, sans toucher au travail des autres. Si quelqu'un a modifié la même propriété entre-temps, elle est conservée et vous êtes prévenu. |
| Ctrl+Z en mode Édition | Annulation native de Blender (l'objet est verrouillé pour vous) ; le résultat est diffusé. |
| ✕ sur une entrée | Annule cette action précise, même ancienne, même faite par un autre joueur. |
| ⟲ sur une entrée | **Revenir à ce point** : toute la scène reprend l'état qu'elle avait juste après cette action. Ce retour s'annule lui-même par Ctrl+Z. |
| Glisser (G/R/S) | Une seule entrée d'historique, pas des centaines. |

Sauvegardes (dossier `~/BlenderMultiplayer`, modifiable dans les préférences) :

- `sessions/<nom>_<date>/checkpoints/` : point de départ et points de sauvegarde manuels ;
- `sessions/<nom>_<date>/autosave/` : autosauvegarde toutes les N minutes (3 par défaut), uniquement s'il y a eu des changements et quand personne n'est en train de manipuler, rotation des N dernières ;
- `sessions/<nom>_<date>/history.jsonl` + `payloads/` : journal complet de l'historique ;
- `backups/` : copie automatique avant de rejoindre, de resynchroniser ou de restaurer ;
- `local_backups/` : copies côté joueurs.

L'hôte peut **restaurer** n'importe quelle sauvegarde : tous les joueurs sont resynchronisés.

## Fonctionnement (résumé technique)

- **Hôte autoritaire** : il numérote, valide (verrous, rôles) et applique chaque opération, l'inscrit dans l'historique puis la rediffuse. Les clients appliquent leurs propres modifications immédiatement (pas de latence ressentie) ; tant qu'elles ne sont pas confirmées, les changements distants sur les mêmes propriétés sont ignorés, ce qui garantit que tout le monde converge vers le même état.
- **Fusion par propriété** : deux joueurs qui modifient des propriétés différentes d'un même objet ne s'écrasent jamais.
- **Identité stable** : chaque data-block porte un identifiant caché (`_mp_uid`) qui survit aux renommages, à la sauvegarde, au chargement ; les doublons (Maj+D) sont détectés.
- **Sans écho** : l'état relu après application d'un changement distant devient l'état connu, il n'est jamais renvoyé. Les propriétés animées ou pilotées par des drivers ne sont pas diffusées (changer de frame ne crée aucune opération).
- Réseau : TCP, trames binaires compressées, aucune dépendance externe (uniquement la bibliothèque standard de Python et numpy, fourni avec Blender).

## Limites connues

- Si l'**hôte quitte**, la session s'arrête (chacun garde sa scène et ses sauvegardes). Il n'y a pas de migration d'hôte.
- Le trafic est authentifié par mot de passe mais **non chiffré** : par Internet, passer par un VPN. Ne rejoindre que des hôtes de confiance (les données échangées sont des fichiers .blend ; l'exécution automatique de scripts est désactivée au chargement).
- Non synchronisés en direct : pixels peints en *Texture Paint* (synchronisés quand l'image est enregistrée/empaquetée), drivers et pistes NLA des objets, ajout de corps rigides/physique par opérateur, séquenceur vidéo, compositing ancien format (4.x).
- Le Ctrl+Z natif reste accessible par le menu *Edit* : en session, il est neutralisé (la scène est remise dans l'état partagé) et un message indique d'utiliser le Ctrl+Z multijoueur.
- Pour une fidélité parfaite, utilisez la même version de Blender (un avertissement s'affiche sinon).

## Tests

```bash
# Hôte et client Blender en arrière-plan, scénario complet, comparaison des scènes
python tests/run_integration.py "<blender hôte>" "<blender client>" 57123      # scénario 1
python tests/run_integration.py "<blender hôte>" "<blender client>" 57124 2    # scénario 2 (types de données)
```

Le scénario 1 couvre la création et la suppression à distance, les matériaux, le Ctrl+Z partagé, les modifications simultanées, le verrou en mode Édition avec la géométrie en direct, les modificateurs, les collections, les parents, le retour à un point et son annulation, les sauvegardes. Le scénario 2 couvre les lumières, les caméras, l'animation, Geometry Nodes, les images, les courbes, l'armature (pose et os), le texte, Maj+D, les conflits de noms simultanés et l'absence d'opération quand on change de frame.
