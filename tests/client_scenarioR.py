import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import render_util as R  # noqa: E402
import scene_audit as A  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "client"
session = T.session


def compare(tag_a, tag_b, eng):
    pa = os.path.join(T.OUT, "%s_%s.png" % (tag_a, eng.lower()))
    pb = os.path.join(T.OUT, "%s_%s.png" % (tag_b, eng.lower()))
    ia = bpy.data.images.load(pa, check_existing=False)
    ib = bpy.data.images.load(pb, check_existing=False)
    a, b = list(ia.pixels), list(ib.pixels)
    bpy.data.images.remove(ia)
    bpy.data.images.remove(ib)
    d = [abs(x - y) for x, y in zip(a, b)]
    return {"max": round(max(d), 3), "mean": round(sum(d) / len(d), 4)}


try:
    if os.environ.get("MP_GUEST_PREP"):
        exec(os.environ["MP_GUEST_PREP"])
    s = None
    for _ in range(80):
        try:
            s = session.join("127.0.0.1", T.PORT, "", username="Client")
            break
        except OSError:
            time.sleep(0.5)
    T.wait_for(lambda: s.active, 300, "sync")
    T.pump(1.0)
    host = json.load(open(os.path.join(T.OUT, "audit_host.json")))
    d = A.diff(host, A.audit())
    T.RESULTS["audit_diff"] = d[:40]
    T.check("materiaux/images/lumieres identiques", not d, d[:20])
    T.RESULTS["render"] = {e: R.render_mean(T.OUT, "guest", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU invite", T.RESULTS["render"])
    for e in ("CYCLES", "EEVEE"):
        c = compare("host", "guest", e)
        T.RESULTS["cmp_" + e] = c
        T.check("rendu %s identique" % e, c["mean"] < 0.02, c)
    T.say("END")
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
