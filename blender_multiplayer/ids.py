"""Identite stable des data-blocks partages.

Chaque data-block synchronise recoit une propriete cachee "_mp_uid" (les
proprietes prefixees par "_" ne s'affichent pas dans l'interface). L'UID
survit aux renommages, a la sauvegarde et au chargement du fichier.

Deux modes de synchronisation :
- "native" / "mesh" : etat lu et applique propriete par propriete (rapide,
  granularite fine, ideal pour les transformations et la geometrie) ;
- "blob" : le data-block complet est ecrit dans un mini .blend
  (bpy.data.libraries.write) puis importe chez les autres. Fidelite totale
  pour les types complexes (materiaux, nodes, animations, armatures...).
"""

import uuid

import bpy

UID_KEY = "_mp_uid"

# collection bpy.data -> (mode, ordre de creation)
_TYPE_TABLE = (
    ("images", "blob", 0),
    ("fonts", "blob", 0),
    ("sounds", "blob", 0),
    ("movieclips", "blob", 0),
    ("texts", "blob", 0),
    ("textures", "blob", 1),
    ("node_groups", "blob", 2),
    ("materials", "blob", 3),
    ("worlds", "blob", 3),
    ("actions", "blob", 3),
    ("linestyles", "blob", 3),
    ("particles", "blob", 3),
    ("meshes", "mesh", 4),
    ("lights", "native", 4),
    ("cameras", "native", 4),
    ("curves", "blob", 4),
    ("armatures", "blob", 4),
    ("lattices", "blob", 4),
    ("metaballs", "blob", 4),
    ("grease_pencils", "blob", 4),
    ("grease_pencils_v3", "blob", 4),
    ("annotations", "blob", 4),
    ("hair_curves", "blob", 4),
    ("pointclouds", "blob", 4),
    ("volumes", "blob", 4),
    ("speakers", "blob", 4),
    ("lightprobes", "blob", 4),
    ("cache_files", "blob", 4),
    ("objects", "native", 6),
    ("collections", "native", 7),
    ("scenes", "native", 8),
)

TYPES = {}
for _attr, _mode, _order in _TYPE_TABLE:
    TYPES[_attr] = (_mode, _order)

# Libelles lisibles pour l'historique
TYPE_LABELS = {
    "images": "image", "fonts": "police", "sounds": "son", "movieclips": "clip",
    "texts": "texte", "textures": "texture", "node_groups": "groupe de nodes",
    "materials": "materiau", "worlds": "monde", "actions": "animation",
    "linestyles": "style de ligne", "particles": "particules", "meshes": "maillage",
    "lights": "lumiere", "cameras": "camera", "curves": "courbe",
    "armatures": "armature", "lattices": "lattice", "metaballs": "metaball",
    "grease_pencils": "grease pencil", "grease_pencils_v3": "grease pencil",
    "annotations": "annotation", "hair_curves": "cheveux", "pointclouds": "nuage de points",
    "volumes": "volume", "speakers": "haut-parleur", "lightprobes": "sonde",
    "cache_files": "cache", "objects": "objet", "collections": "collection",
    "scenes": "scene",
}

_ID_TYPE_TO_ATTR = {
    "OBJECT": "objects", "MESH": "meshes", "MATERIAL": "materials", "IMAGE": "images",
    "NODETREE": "node_groups", "WORLD": "worlds", "ACTION": "actions", "LIGHT": "lights",
    "CAMERA": "cameras", "CURVE": "curves", "ARMATURE": "armatures", "LATTICE": "lattices",
    "META": "metaballs", "CURVES": "hair_curves", "POINTCLOUD": "pointclouds",
    "VOLUME": "volumes", "SPEAKER": "speakers", "LIGHT_PROBE": "lightprobes",
    "CACHEFILE": "cache_files", "COLLECTION": "collections", "SCENE": "scenes",
    "TEXT": "texts", "TEXTURE": "textures", "FONT": "fonts", "SOUND": "sounds",
    "MOVIECLIP": "movieclips", "PARTICLE": "particles", "LINESTYLE": "linestyles",
    "BRUSH": "brushes", "PALETTE": "palettes", "MASK": "masks", "KEY": "shape_keys",
    "WORKSPACE": "workspaces", "SCREEN": "screens", "PAINTCURVE": "paint_curves",
    "GREASEPENCIL": "grease_pencils", "GREASEPENCIL_V3": "grease_pencils_v3",
}

_SKIP_IMAGE_TYPES = {"RENDER_RESULT", "COMPOSITING"}


def active_types():
    """Collections bpy.data existant dans cette version de Blender."""
    out = []
    seen_types = set()
    for attr, _mode, _order in _TYPE_TABLE:
        coll = getattr(bpy.data, attr, None)
        if coll is None:
            continue
        # grease_pencils et grease_pencils_v3 peuvent pointer sur le meme type
        try:
            key = coll.rna_type.identifier if hasattr(coll, "rna_type") else attr
        except Exception:
            key = attr
        if key in seen_types and attr != "objects":
            continue
        seen_types.add(key)
        out.append(attr)
    return out


def mode_of(attr):
    return TYPES.get(attr, ("blob", 5))[0]


def order_of(attr):
    return TYPES.get(attr, ("blob", 5))[1]


def label_of(attr):
    return TYPE_LABELS.get(attr, attr)


def new_uid():
    return uuid.uuid4().hex[:16]


def get_uid(idb):
    if idb is None:
        return None
    try:
        v = idb.get(UID_KEY)
    except Exception:
        return None
    return v if isinstance(v, str) else None


def set_uid(idb, uid):
    try:
        idb[UID_KEY] = uid
        return True
    except Exception:
        return False


def trackable(attr, idb):
    try:
        if idb.library is not None:
            return False
        if getattr(idb, "is_runtime_data", False):
            return False
        if attr == "images" and idb.type in _SKIP_IMAGE_TYPES:
            return False
        if getattr(idb, "is_embedded_data", False):
            return False
    except ReferenceError:
        return False
    return True


def attr_of_id(idb):
    try:
        return _ID_TYPE_TO_ATTR.get(idb.id_type)
    except Exception:
        return None


def remove_id(attr, idb):
    coll = getattr(bpy.data, attr, None)
    if coll is None:
        return False
    try:
        if attr == "objects":
            coll.remove(idb, do_unlink=True)
        else:
            coll.remove(idb)
        return True
    except Exception:
        try:
            bpy.data.batch_remove([idb])
            return True
        except Exception:
            return False
