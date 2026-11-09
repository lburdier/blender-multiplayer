"""Edition a plusieurs : securite pendant une vraie operation interactive (fenetre reelle).

blender --factory-startup --enable-event-simulate --python gui_coedit.py -- <parent> <dossier>

Le cube passe en mode Edition, un vrai deplacement G (modal) demarre. Pendant
qu'il tourne : des sommets recus en direct sont ecrits (pas de plantage), une
subdivision recue est mise en attente (pas de BMesh remplace sous le G). Apres
Echap, la subdivision s'applique. Resultat dans gui_coedit.json.
"""

import json
import os
import sys
import threading

import bmesh
import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
OUT = ARGS[1]

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import coedit, payloads, serialize  # noqa: E402

bpy.context.preferences.view.show_splash = False
mp.register()
threading.Timer(60.0, lambda: os._exit(3)).start()  # jamais de fenetre oubliee
R = {"steps": []}


class Ctx:
    known = {}
    store = payloads.PayloadStore()
    live = None


class FakeLive:
    mesh_tx = {}
    tx_hold = {}


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return win, area, region


def finish():
    with open(os.path.join(OUT, "gui_coedit.json"), "w") as fh:
        json.dump(R, fh)
    os._exit(0)


STATE = {"n": 0}


def step():
    n = STATE["n"]
    STATE["n"] += 1
    win, area, region = view3d()
    obj = bpy.data.objects["Cube"]
    me = obj.data
    try:
        if n == 0:
            with bpy.context.temp_override(window=win, area=area, region=region, active_object=obj,
                                           object=obj):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
            return 0.5
        if n == 1:
            # souris au centre de la vue, puis G : deplacement modal reel
            x = region.x + region.width // 2
            y = region.y + region.height // 2
            win.event_simulate("MOUSEMOVE", "NOTHING", x=x, y=y)
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.transform.translate("INVOKE_DEFAULT")
            return 0.5
        if n == 2:
            mods = [op.bl_idname for op in win.modal_operators]
            R["modal"] = mods
            R["busy_during_G"] = coedit.busy()
            # sommets recus en direct pendant le G : ecriture sans changement de topologie
            nv = len(me.vertices)
            co = np.zeros(nv * 3, np.float32)
            me_co = np.empty(nv * 3, np.float32)
            obj.update_from_editmode()
            me.vertices.foreach_get("co", me_co)
            co[:] = me_co
            co[2] += 0.5
            coedit.apply_live(me, [(nv, None, None, co)], FakeLive(), "x")
            R["live_during_G"] = "ok"
            # subdivision recue pendant le G : doit attendre
            src = me.copy()
            bm = bmesh.new()
            bm.from_mesh(src)
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
            bm.to_mesh(src)
            bm.free()
            R["src_n"] = len(src.vertices)
            STATE["src"] = src.name
            try:
                coedit.merge(me, src, Ctx)
                R["topo_during_G"] = "applique (DANGER)"
            except serialize.Unresolved:
                R["topo_during_G"] = "en attente"
            # le G continue : la souris bouge encore
            win.event_simulate("MOUSEMOVE", "NOTHING", x=region.x + region.width // 2 + 40,
                               y=region.y + region.height // 2 + 20)
            return 0.5
        if n == 3:
            win.event_simulate("ESC", "PRESS")
            win.event_simulate("ESC", "RELEASE")
            return 0.5
        if n == 4:
            R["busy_after_esc"] = coedit.busy()
            src = bpy.data.meshes[STATE["src"]]
            coedit.merge(me, src, Ctx)
            bm = bmesh.from_edit_mesh(me)
            R["after_n"] = len(bm.verts)
            R["still_edit"] = obj.mode
            R["selected_after"] = sum(1 for v in bm.verts if v.select)
            return 0.3
        finish()
    except Exception as exc:  # noqa: BLE001
        import traceback
        R["error"] = traceback.format_exc()
        finish()
    return None


bpy.app.timers.register(step, first_interval=2.0)
