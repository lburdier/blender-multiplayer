"""Petites aides de compatibilite entre versions de Blender (4.2 -> 5.x).

Tout ce qui depend de la version ou du contexte (fenetre, zone 3D, shaders)
passe par ici, pour que le reste du code reste identique quelle que soit la
version utilisee.
"""

import os
import tempfile

import bpy

VERSION = tuple(bpy.app.version)
VERSION_STR = ".".join(str(v) for v in VERSION[:3])


def has_rna_prop(rna_type, name):
    try:
        return name in rna_type.bl_rna.properties
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fenetres / contexte
# ---------------------------------------------------------------------------

def windows():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return []
    try:
        return list(wm.windows)
    except Exception:
        return []


def main_window():
    win = getattr(bpy.context, "window", None)
    if win is not None:
        return win
    ws = windows()
    return ws[0] if ws else None


def largest_view3d(win=None):
    """Retourne (window, area, region, space) de la plus grande vue 3D."""
    wins = [win] if win is not None else windows()
    best = None
    for w in wins:
        if w is None or w.screen is None:
            continue
        for area in w.screen.areas:
            if area.type != "VIEW_3D":
                continue
            size = area.width * area.height
            if best is None or size > best[0]:
                best = (size, w, area)
    if best is None:
        return None, None, None, None
    _, w, area = best
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    return w, area, region, area.spaces.active


def iter_view3d():
    for w in windows():
        if w.screen is None:
            continue
        for area in w.screen.areas:
            if area.type == "VIEW_3D":
                yield w, area


def current_scene():
    win = main_window()
    if win is not None and win.scene is not None:
        return win.scene
    return bpy.context.scene


def current_view_layer():
    win = main_window()
    if win is not None:
        try:
            return win.view_layer
        except Exception:
            pass
    vl = getattr(bpy.context, "view_layer", None)
    if vl is not None:
        return vl
    sc = current_scene()
    return sc.view_layers[0] if sc and sc.view_layers else None


def call_op(op, view3d=False, **kwargs):
    """Appelle un operateur avec un contexte de fenetre valide.

    Depuis un timer, bpy.context n'a pas de fenetre : on en fournit une.
    """
    if bpy.app.background:
        return op(**kwargs)
    win = main_window()
    if win is None:
        return op(**kwargs)
    override = {"window": win, "screen": win.screen}
    if view3d:
        w, area, region, _space = largest_view3d(win)
        if area is not None:
            override["area"] = area
            override["region"] = region
    with bpy.context.temp_override(**override):
        return op(**kwargs)


def tag_redraw(types=("VIEW_3D", "OUTLINER", "PROPERTIES", "NODE_EDITOR",
                      "DOPESHEET_EDITOR", "GRAPH_EDITOR", "IMAGE_EDITOR")):
    for w in windows():
        if w.screen is None:
            continue
        for area in w.screen.areas:
            if area.type in types:
                area.tag_redraw()


def modal_running():
    """Vrai si un operateur modal (deplacement, rotation...) est en cours."""
    for w in windows():
        mods = getattr(w, "modal_operators", None)
        if mods is not None:
            try:
                if len(mods):
                    return True
            except Exception:
                pass
    return False


# ---------------------------------------------------------------------------
# Animation (actions "legacy" < 4.4 et actions a slots/calques >= 4.4 / 5.x)
# ---------------------------------------------------------------------------

def anim_fcurves(ad):
    """F-curves qui pilotent un data-block : action (slot courant) + drivers."""
    out = []
    if ad is None:
        return out
    act = getattr(ad, "action", None)
    if act is not None:
        legacy = None
        if VERSION < (5, 0, 0):
            legacy = getattr(act, "fcurves", None)
        if legacy is not None:
            try:
                out.extend(legacy)
            except Exception:
                pass
        else:
            slot = getattr(ad, "action_slot", None)
            for layer in getattr(act, "layers", ()):
                for strip in getattr(layer, "strips", ()):
                    for cb in getattr(strip, "channelbags", ()):
                        try:
                            if slot is None or cb.slot == slot:
                                out.extend(cb.fcurves)
                        except Exception:
                            continue
    try:
        out.extend(ad.drivers)
    except Exception:
        pass
    return out


def has_animation(idb):
    for holder in (idb, getattr(idb, "node_tree", None), getattr(idb, "shape_keys", None)):
        ad = getattr(holder, "animation_data", None) if holder is not None else None
        if ad is not None and (ad.action is not None or len(ad.drivers) or len(ad.nla_tracks)):
            return True
    return False


# ---------------------------------------------------------------------------
# Fichiers
# ---------------------------------------------------------------------------

def temp_dir():
    path = os.path.join(tempfile.gettempdir(), "blender_mp", str(os.getpid()))
    os.makedirs(path, exist_ok=True)
    return path


def save_copy(filepath):
    """Sauvegarde une copie du fichier courant sans changer de fichier actif."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    kwargs = dict(filepath=filepath, copy=True, compress=True)
    try:
        call_op(bpy.ops.wm.save_as_mainfile, relative_remap=False, **kwargs)
    except TypeError:
        call_op(bpy.ops.wm.save_as_mainfile, **kwargs)
    return filepath


def save_as(filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    call_op(bpy.ops.wm.save_as_mainfile, filepath=filepath, compress=True)


def open_file(filepath):
    """Ouvre un .blend en conservant l'interface et sans executer de scripts."""
    try:
        return call_op(bpy.ops.wm.open_mainfile, filepath=filepath,
                       load_ui=False, use_scripts=False)
    except TypeError:
        return call_op(bpy.ops.wm.open_mainfile, filepath=filepath, load_ui=False)


def libraries_write(filepath, datablocks):
    for remap in ("ABSOLUTE", True):
        try:
            bpy.data.libraries.write(filepath, set(datablocks), path_remap=remap,
                                     fake_user=False, compress=True)
            return
        except (TypeError, ValueError):
            continue
    bpy.data.libraries.write(filepath, set(datablocks))


# ---------------------------------------------------------------------------
# Dessin
# ---------------------------------------------------------------------------

_shader_cache = {}


def line_shader():
    """Shader de lignes epaisses si disponible (4.x/5.x), sinon basique."""
    if "line" in _shader_cache:
        return _shader_cache["line"]
    import gpu
    res = None
    for name, thick in (("POLYLINE_UNIFORM_COLOR", True), ("UNIFORM_COLOR", False),
                        ("3D_UNIFORM_COLOR", False)):
        try:
            res = (gpu.shader.from_builtin(name), thick)
            break
        except Exception:
            continue
    _shader_cache["line"] = res
    return res


def flat_shader():
    if "flat" in _shader_cache:
        return _shader_cache["flat"]
    import gpu
    res = None
    for name in ("UNIFORM_COLOR", "3D_UNIFORM_COLOR"):
        try:
            res = gpu.shader.from_builtin(name)
            break
        except Exception:
            continue
    _shader_cache["flat"] = res
    return res


def blf_size(font_id, size):
    import blf
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, int(size), 72)
