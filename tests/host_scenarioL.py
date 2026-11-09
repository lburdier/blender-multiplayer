"""Scenario L (hote) : voir l_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import l_common as L  # noqa: E402

T.ROLE = "host"
try:
    L.build_scene()
    s = T.session.host(T.PORT, "secret", "Shading", username="Hote")
    T.pump(1.0)
    open(os.path.join(T.OUT, "host_ready.flag"), "w").close()
    T.wait_for(lambda: len(s.users) == 2, 120, "client")
    T.barrier("JOINED", 120)
    L.publish(T, "join")
    T.say("JOIN")
    T.barrier("JOIN_OK", 60)
    L.editor(T, "H")
    L.viewer(T, "C")
    T.say("END_H")
    T.barrier("END_C", 60)
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
