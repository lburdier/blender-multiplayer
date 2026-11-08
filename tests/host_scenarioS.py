"""Soak (hote) : scene reelle, rien ne doit s'ajouter a l'historique a vide."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session


def entries_since(s, seq0):
    out = []
    for e in s.history.entries:
        if e["seq"] <= seq0:
            continue
        fields = {}
        for c in e["changes"]:
            fields.setdefault(c["t"], set()).update((c.get("a") or {}).keys() if c["k"] == "u" else {c["k"]})
        out.append({"seq": e["seq"], "user": e["uname"], "label": e["label"],
                    "fields": {k: sorted(v) for k, v in fields.items()}})
    return out


try:
    T.log("scene", bpy.data.filepath, len(bpy.data.objects), "objets")
    t0 = time.time()
    s = session.host(T.PORT, "", "Soak", username="Hote")
    T.log("demarrage hote %.1f s" % (time.time() - t0))
    T.wait_for(lambda: len(s.users) == 2, 120, "client")
    T.barrier("C0", 300)
    seq0 = s.history.seq
    T.pump(15.0)
    idle = entries_since(s, seq0)
    T.RESULTS["idle"] = idle
    T.check("aucune action a vide", len(idle) == 0, idle[:10])
    # L'hote deplace un objet pendant 2 s
    movable = [o for o in bpy.context.view_layer.objects if o.type in ("MESH", "EMPTY") and not o.animation_data
               and not o.constraints and o.parent is None]
    ob = movable[0] if movable else None
    seq1 = s.history.seq
    if ob is not None:
        x0 = ob.location.x
        for i in range(60):
            ob.location.x = x0 + i * 0.02
            T.pump(0.033)
    T.pump(3.0)
    mv = entries_since(s, seq1)
    T.RESULTS["host_move"] = mv
    T.check("deplacement hote = 1 action", len(mv) <= 1, mv[:10])
    T.say("H1")
    T.barrier("C1", 120)
    seq2 = s.history.seq
    T.pump(6.0)
    cm = entries_since(s, seq2)
    T.RESULTS["after_client_move"] = cm
    T.check("pas d'action apres le deplacement de l'invite", len(cm) == 0, cm[:10])
    T.say("END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()
