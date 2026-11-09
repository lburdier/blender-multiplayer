"""Canal temps reel dans de vraies fenetres Blender (minuteurs et affichage reels).

blender --factory-startup --python gui_live.py -- <parent> <host|guest> <port> <dossier>

L'invite passe le cube en mode Edition et deplace un sommet pendant 4 s. L'hote
note a quel instant il voit chaque valeur, fait une capture de sa vue 3D pendant
le geste et une apres, puis ecrit les statistiques dans live_gui.json.
"""

import json
import os
import statistics
import sys
import threading
import time

import bmesh
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE, PORT, OUT = ARGS[1], int(ARGS[2]), ARGS[3]

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

bpy.context.preferences.view.show_splash = False
mp.register()
threading.Timer(90.0, lambda: os._exit(3)).start()  # jamais de fenetre oubliee
T0 = time.time()
ST = {"step": 0, "seen": [], "last": None, "tx": []}
TX = os.path.join(OUT, "tx.json")
DONE = os.path.join(OUT, "live_gui.json")


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return win, area, region


def shot(name):
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, name))


def set_mode(mode):
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.object.mode_set(mode=mode)


def cube_z():
    ob = bpy.data.objects["Cube"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    return round(ob.data.vertices[0].co.z, 4)


def guest():
    st = ST
    s = session.current
    if st["step"] == 0 and time.time() - T0 > 4.0:
        session.join("127.0.0.1", PORT, "", username="Invite")
        st["step"] = 1
    elif st["step"] == 1 and s and s.active and not s.incoming:
        st["t"] = time.time() + 2.0
        st["step"] = 2
    elif st["step"] == 2 and time.time() > st["t"]:
        set_mode("EDIT")
        st["t"] = time.time() + 2.0
        st["step"] = 3
    elif st["step"] == 3 and time.time() > st["t"]:
        st["t"], st["i"], st["next"] = time.time(), 0, 0.0
        st["step"] = 4
    elif st["step"] == 4:
        now = time.time()
        if now - st["t"] > 4.0:
            with open(TX, "w") as fh:
                json.dump(st["tx"], fh)
            st["t"] = now + 1.5
            st["step"] = 5
        elif now >= st["next"]:
            st["next"] = now + 1.0 / 30.0
            st["i"] += 1
            value = round(-1.0 + st["i"] * 0.01, 4)
            me = bpy.data.objects["Cube"].data
            bm = bmesh.from_edit_mesh(me)
            bm.verts.ensure_lookup_table()
            bm.verts[0].co.z = value
            bmesh.update_edit_mesh(me)
            st["tx"].append((now, value))
    elif st["step"] == 5 and time.time() > st["t"]:
        set_mode("OBJECT")
        st["step"] = 6
    elif st["step"] == 6 and os.path.exists(DONE):
        bpy.ops.wm.quit_blender()
        return None
    return 0.005


def host():
    st = ST
    s = session.current
    if st["step"] == 0 and time.time() - T0 > 1.0:
        session.host(PORT, "", "Live", username="Hote")
        st["step"] = 1
    elif st["step"] == 1 and s and len(s.users) == 2 and s.locks:
        st["last"] = cube_z()
        st["step"] = 2
    elif st["step"] == 2:
        z = cube_z()
        if z != st["last"]:
            st["seen"].append((time.time(), z))
            st["last"] = z
            if len(st["seen"]) == 60:
                shot("pendant.png")
        if os.path.exists(TX):
            st["t"] = time.time() + 3.5
            st["step"] = 3
    elif st["step"] == 3 and time.time() > st["t"]:
        shot("apres.png")
        with open(TX) as fh:
            tx = json.load(fh)
        when = {round(v, 4): t for t, v in tx}
        lat = sorted((t - when[z]) * 1000 for t, z in st["seen"] if z in when)
        stats = {"envoyes": len(tx), "recus": len(lat),
                 "mediane_ms": round(statistics.median(lat)) if lat else None,
                 "p90_ms": round(lat[int(len(lat) * 0.9)]) if lat else None,
                 "max_ms": round(lat[-1]) if lat else None,
                 "historique": [e["label"] for e in s.history.entries][-6:],
                 "z_final": cube_z(), "z_envoye": tx[-1][1]}
        with open(DONE, "w") as fh:
            json.dump(stats, fh)
        st["t"] = time.time() + 2.0
        st["step"] = 4
    elif st["step"] == 4 and time.time() > st["t"]:
        bpy.ops.wm.quit_blender()
        return None
    return 0.005


def driver():
    try:
        return guest() if ROLE == "guest" else host()
    except Exception:
        import traceback
        traceback.print_exc()
        return 0.05


bpy.app.timers.register(driver, first_interval=0.5, persistent=True)
