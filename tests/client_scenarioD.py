import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import render_util as R  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "client"
session = T.session

try:
    s = None
    for _ in range(40):
        try:
            s = session.join("127.0.0.1", T.PORT, "", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 90, "sync")
    T.pump(1.0)
    sc = bpy.context.scene
    info = {"engine": sc.render.engine, "world": sc.world.name if sc.world else None,
            "lights": [(o.name, o.data.type, o.data.energy, o.hide_render, o.hide_viewport) for o in bpy.data.objects if o.type == "LIGHT"],
            "view": (sc.view_settings.view_transform, sc.view_settings.exposure, sc.view_settings.look),
            "cyc_exposure": getattr(sc.cycles, "film_exposure", None),
            "images": [(i.name, i.filepath, tuple(i.size), i.has_data) for i in bpy.data.images],
            "film_transparent": sc.render.film_transparent}
    T.log("INFO", info)
    T.RESULTS["info0"] = info
    r0 = {e: R.render_mean(T.OUT, "client0", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU client initial", r0)
    T.say("C0")
    T.barrier("H1", 60)
    T.pump(1.0)
    r1 = {e: R.render_mean(T.OUT, "client1", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU client apres soleil hote", r1)
    T.RESULTS["lights1"] = [(o.name, o.data.type, o.data.energy) for o in bpy.data.objects if o.type == "LIGHT"]
    bpy.ops.object.light_add(type="AREA", location=(0, 0, 3))
    bpy.context.active_object.data.energy = 2000.0
    bpy.context.active_object.name = "ZoneClient"
    T.pump(1.5)
    T.say("C1")
    T.barrier("END", 120)
    T.RESULTS["renders"] = {"r0": r0, "r1": r1}
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()
