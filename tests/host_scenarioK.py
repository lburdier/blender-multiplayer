"""Scenario K (hote) : voir k_common. Scene : MP_SCENE = le Jeep (rig JC_Rig)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import k_common as K  # noqa: E402

T.ROLE = "host"
try:
    s = T.session.host(T.PORT, "secret", "Addon", username="Hote")
    phase = ["join"]
    sent = K.watch_sends(T, s.tracker, phase)
    T.pump(1.0)
    open(os.path.join(T.OUT, "host_ready.flag"), "w").close()
    T.wait_for(lambda: len(s.users) == 2, 300, "client")
    T.barrier("JOINED", 300)
    if K.DRIVER == "host":
        K.drive(T, phase, sent)
    else:
        K.watch(T, phase, sent)
        T.pump(2.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
