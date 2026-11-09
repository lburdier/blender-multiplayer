"""Scenario L (invite) : voir l_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import l_common as L  # noqa: E402

T.ROLE = "client"
try:
    T.wait_for(lambda: os.path.exists(os.path.join(T.OUT, "host_ready.flag")), 120, "hote pret")
    s = None
    for _ in range(80):
        try:
            s = T.session.join("127.0.0.1", T.PORT, "secret", username="Client")
            break
        except OSError:
            T.pump(0.5)
    T.wait_for(lambda: s.active and not s.incoming, 120, "sync")
    T.pump(1.0)
    T.say("JOINED")
    T.barrier("JOIN", 60)
    L.compare(T, "join")
    T.say("JOIN_OK")
    L.viewer(T, "H")
    L.editor(T, "C")
    T.barrier("END_H", 60)
    T.say("END_C")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
