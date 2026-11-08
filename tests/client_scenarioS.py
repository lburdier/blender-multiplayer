import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "client"
session = T.session

try:
    s = None
    for _ in range(80):
        try:
            s = session.join("127.0.0.1", T.PORT, "", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 300, "sync")
    T.pump(1.0)
    T.say("C0")
    T.barrier("H1", 120)
    movable = [o for o in bpy.context.view_layer.objects if o.type in ("MESH", "EMPTY") and not o.animation_data
               and not o.constraints and o.parent is None]
    ob = movable[-1] if movable else None
    seq = max(s.hist) if s.hist else 0
    if ob is not None:
        y0 = ob.location.y
        for i in range(60):
            ob.location.y = y0 + i * 0.02
            T.pump(0.033)
    T.pump(3.0)
    mine = [h for k, h in s.hist.items() if k > seq]
    T.RESULTS["client_move"] = [h.get("l") for h in mine]
    T.check("deplacement invite = 1 action", len(mine) <= 1, [h.get("l") for h in mine])
    T.say("C1")
    T.barrier("END", 120)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()
