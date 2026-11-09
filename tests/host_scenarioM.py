"""Scenario M (hote) : voir m_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import m_common as M  # noqa: E402

T.ROLE = "host"
try:
    M.build_scene()
    s = T.session.host(T.PORT, "secret", "Latence", username="Hote")
    phase = ["join"]
    sent = M.watch_sends(s.tracker, phase)
    T.pump(1.0)
    open(os.path.join(T.OUT, "host_ready.flag"), "w").close()
    T.wait_for(lambda: len(s.users) == 2, 300, "client")
    T.barrier("JOINED", 300)
    phase[0] = "run"
    if M.DRIVER == "host":
        M.driver(T)
    else:
        M.viewer(T, sent)
    T.say("END_H")
    T.barrier("END_C", 60)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
