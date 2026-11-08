"""Banc d'essai : cout Python d'un tick de session pendant un deplacement.

blender -b --factory-startup --python bench_tick.py -- <parent addon> <port>
Scene : 6 hierarchies de 50 enfants (biseau), 300 objets ; on deplace la
racine de la premiere hierarchie a 60 i/s pendant 3 s, en temps reel.
"""
import sys
import time

ARGS = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, ARGS[0])
import bpy  # noqa: E402
import blender_multiplayer as mp  # noqa: E402
from blender_multiplayer import session  # noqa: E402

mp.register()
roots = []
for h in range(6):
    bpy.ops.object.empty_add(location=(h * 10, 0, 0))
    root = bpy.context.active_object
    root.name = "Racine%d" % h
    roots.append(root)
    for i in range(50):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=(h * 10 + i % 7, i // 7, 0))
        o = bpy.context.active_object
        o.parent = root
        o.modifiers.new("Biseau", "BEVEL")
bpy.context.view_layer.update()
s = session.host(int(ARGS[1]), "", "Banc", username="Hote")
for _ in range(10):
    bpy.context.view_layer.update()
    s.tick()
py, ev = [], []
t_end = time.time() + 3.0
x = 0.0
while time.time() < t_end:
    x += 0.01
    roots[0].location.x = x
    t0 = time.perf_counter()
    bpy.context.view_layer.update()
    t1 = time.perf_counter()
    s.tick()
    t2 = time.perf_counter()
    ev.append(t1 - t0)
    py.append(t2 - t1)
    time.sleep(max(0.0, 1 / 60 - (t2 - t0)))
py.sort()
n = len(py)
print("BANC ticks=%d python_moyen=%.2f ms median=%.2f ms p95=%.2f ms max=%.2f ms | eval_blender_moyen=%.2f ms | historique=%d"
      % (n, 1000 * sum(py) / n, 1000 * py[n // 2], 1000 * py[int(n * 0.95)], 1000 * py[-1], 1000 * sum(ev) / n,
         len(s.history.entries)), flush=True)
s.stop()
