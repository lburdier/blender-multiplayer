"""Rendu d'une vraie scene : l'invite doit obtenir la meme image que l'hote."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import render_util as R  # noqa: E402
import scene_audit as A  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session
try:
    sc = bpy.context.scene
    if sc.camera is None:
        bpy.ops.object.camera_add(location=(8, -8, 6), rotation=(1.1, 0, 0.785))
        sc.camera = bpy.context.active_object
    with open(os.path.join(T.OUT, "audit_host.json"), "w") as fh:
        json.dump(A.audit(), fh)
    T.RESULTS["render"] = {e: R.render_mean(T.OUT, "host", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU hote", T.RESULTS["render"])
    s = session.host(T.PORT, "", "Rendu", username="Hote")
    T.wait_for(lambda: len(s.users) == 2, 120, "client")
    T.barrier("END", 400)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
