"""Scenario E (hote) : voir e_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import e_common as E  # noqa: E402

T.ROLE = "host"
try:
    E.build_scene()
    s = T.session.host(T.PORT, "secret", "CoEdition", username="Hote")
    phase = ["join"]
    sent = E.watch_sends(s.tracker, phase)
    T.pump(1.0)
    open(os.path.join(T.OUT, "host_ready.flag"), "w").close()
    T.wait_for(lambda: len(s.users) == 2, 300, "client")
    T.barrier("JOINED", 300)
    E.run(T, sent, phase)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
