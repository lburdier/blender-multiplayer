"""Mesure de la fluidite en mode graphique (minuteurs reels de Blender).

blender --factory-startup --python gui_smooth.py -- <parent> <host|guest> <port> <dossier> <0|1>

L'invite deplace le cube en continu pendant 4 s ; l'hote echantillonne sa
position a 60 Hz et ecrit les statistiques : nombre de positions differentes
par seconde et plus grand saut entre deux images.
"""

import json
import math
import os
import sys
import time

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
ROLE, PORT, OUT, SMOOTH = ARGS[1], int(ARGS[2]), ARGS[3], ARGS[4] == "1"

import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.prefs._defaults.smooth_motion = SMOOTH
mp.register()
T0 = time.time()
ST = {"step": 0, "samples": []}


def sampler():
    cube = bpy.data.objects.get("Cube")
    if cube is not None and ST.get("sampling"):
        ST["samples"].append((time.time(), cube.location.x))
    return 1.0 / 60.0


def driver():
    t = time.time() - T0
    s = session.current
    try:
        if ST["step"] == 0 and t > 1.0:
            if ROLE == "host":
                session.host(PORT, "", "Fluidite", username="Hote")
            ST["step"] = 1
        elif ST["step"] == 1 and ROLE == "guest" and t > 4.0:
            session.join("127.0.0.1", PORT, "", username="Invite")
            ST["step"] = 2
        elif ST["step"] == 2 and ROLE == "guest" and s and s.active:
            ST["step"] = 3
            ST["t3"] = time.time() + 1.0
        elif ST["step"] == 3 and ROLE == "guest":
            k = time.time() - ST["t3"]
            if 0 <= k <= 4.0:
                bpy.data.objects["Cube"].location.x = 3.0 * math.sin(k * 1.5)
            elif k > 6.0:
                bpy.ops.wm.quit_blender()
                return None
        elif ST["step"] == 1 and ROLE == "host" and s and len(s.users) == 2:
            ST["step"] = 2
            ST["t2"] = time.time()
        elif ST["step"] == 2 and ROLE == "host":
            k = time.time() - ST["t2"]
            ST["sampling"] = 1.5 < k < 5.5
            if k > 7.0:
                xs = [x for _t, x in ST["samples"]]
                moves = [abs(b - a) for a, b in zip(xs, xs[1:])]
                dur = ST["samples"][-1][0] - ST["samples"][0][0] if len(ST["samples"]) > 1 else 1
                stats = {"lissage": SMOOTH, "echantillons": len(xs),
                         "positions_differentes_par_s": round(sum(1 for m in moves if m > 1e-6) / dur, 1),
                         "images_figees_pct": round(100.0 * sum(1 for m in moves if m <= 1e-6) / max(1, len(moves)), 1),
                         "plus_grand_saut": round(max(moves) if moves else 0, 3),
                         "saut_moyen": round(sum(moves) / max(1, len(moves)), 4),
                         "historique": len(s.history.entries),
                         "libelles": [e["label"] for e in s.history.entries][-8:]}
                with open(os.path.join(OUT, "smooth_%d.json" % SMOOTH), "w") as fh:
                    json.dump(stats, fh)
                bpy.ops.wm.quit_blender()
                return None
    except Exception:
        import traceback
        traceback.print_exc()
    return 0.01


bpy.app.timers.register(driver, first_interval=0.5, persistent=True)
bpy.app.timers.register(sampler, first_interval=0.5, persistent=True)
