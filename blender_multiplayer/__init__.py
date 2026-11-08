"""Blender Multiplayer : editer la meme scene a plusieurs, en temps reel.

Ouvrir la barre laterale de la vue 3D (touche N) > onglet "Multijoueur".
"""

bl_info = {
    "name": "Blender Multiplayer",
    "author": "Maxence Marrot",
    "version": (1, 6, 0),
    "blender": (4, 2, 0),
    "doc_url": "https://github.com/lburdier/blender-multiplayer",
    "location": "Vue 3D > Barre laterale (N) > Multijoueur",
    "description": "Edition collaborative en temps reel de la meme scene a plusieurs",
    "category": "System",
}

if "bpy" in locals():
    import importlib
    for _m in ("compat", "net", "ids", "payloads", "fingerprint", "serialize", "tracker",
               "history", "presence", "smooth", "tunnel", "invite", "session", "updater", "prefs",
               "operators", "ui"):
        if _m in locals():
            importlib.reload(locals()[_m])

import bpy
from bpy.app.handlers import persistent

from . import (compat, fingerprint, history, ids, invite, net, operators, payloads, prefs,
               presence, serialize, session, smooth, tracker, tunnel, ui, updater)


@persistent
def _on_depsgraph(scene, depsgraph=None):
    s = session.current
    if s is None or not s.active or s.loading or depsgraph is None:
        return
    s.tracker.on_depsgraph(depsgraph)


@persistent
def _on_undo(*_args):
    s = session.current
    if s is not None:
        s.on_native_undo()


@persistent
def _on_load_post(*_args):
    s = session.current
    if s is not None:
        s.on_load_post()


_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph),
    ("undo_post", _on_undo),
    ("redo_post", _on_undo),
    ("load_post", _on_load_post),
)

_CLASSES = prefs.classes + ui.classes + operators.classes


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    ui.register()
    presence.register()
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)
    if not bpy.app.background:
        operators.register_keymaps()
    updater.register()
    # Une ancienne version (avant mise a jour) peut avoir laisse une session active.
    try:
        session.stop_orphans()
    except Exception:  # noqa: BLE001
        pass


def unregister():
    updater.unregister()
    if session.current is not None:
        try:
            session.current.stop("addon desactive")
        except Exception:
            pass
        session.current = None
    operators.unregister_keymaps()
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    presence.unregister()
    ui.unregister()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
