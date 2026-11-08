"""Profil d'un deplacement d'objet en session (hote seul, arriere-plan).

blender -b scene.blend --python profile_move.py -- <parent addon> <port> <dossier>
"""
import cProfile
import io
import os
import pstats
import sys
import time

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
import bpy  # noqa: E402
import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()
OUT = ARGS[2]
t = time.time()
s = session.host(int(ARGS[1]), "", "Profil", username="Hote")
print("demarrage %.2f s" % (time.time() - t), flush=True)
movable = [o for o in bpy.context.view_layer.objects if o.type in ("MESH", "EMPTY") and not o.animation_data
           and not o.constraints and o.parent is None]
ob = movable[0]
print("objet", ob.name, ob.type, len(ob.data.vertices) if ob.type == "MESH" else 0, flush=True)
for i in range(5):
    bpy.context.view_layer.update()
    s.tick()
pr = cProfile.Profile()
times = []
pr.enable()
for i in range(40):
    ob.location.x += 0.02
    t = time.time()
    bpy.context.view_layer.update()
    s.tick()
    times.append(time.time() - t)
pr.disable()
times.sort()
print("tick median %.1f ms, max %.1f ms, total %.2f s" % (1000 * times[len(times) // 2], 1000 * times[-1], sum(times)))
st = io.StringIO()
pstats.Stats(pr, stream=st).sort_stats("cumulative").print_stats(28)
print(st.getvalue()[:6000])
s.stop()
