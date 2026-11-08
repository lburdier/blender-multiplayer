"""Scenario K (invite) : voir k_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import k_common as K  # noqa: E402

T.ROLE = "client"
try:
    T.wait_for(lambda: os.path.exists(os.path.join(T.OUT, "host_ready.flag")), 300, "hote pret")
    s = None
    for _ in range(80):
        try:
            s = T.session.join("127.0.0.1", T.PORT, "secret", username="Client")
            break
        except OSError:
            T.pump(0.5)
    T.wait_for(lambda: s.active and not s.incoming, 600, "sync")
    phase = ["join"]
    sent = K.watch_sends(T, s.tracker, phase)
    T.pump(2.0)
    T.say("JOINED")
    if K.DRIVER == "client":
        K.drive(T, phase, sent)
    else:
        K.watch(T, phase, sent)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
