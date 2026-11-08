"""Inactivite (hote) : rien ne doit circuler ni se mettre a jour."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import probe_util as P  # noqa: E402

T.ROLE = "host"
session = T.session
try:
    s = session.host(T.PORT, "", "Idle", username="Hote")
    P.install(s)
    T.wait_for(lambda: len(s.users) == 2, 120, "client")
    T.barrier("C0", 300)
    T.pump(3.0)
    P.ON[0] = True
    T.pump(15.0)
    P.ON[0] = False
    T.RESULTS["probe"] = P.report()
    T.log("PROBE", P.report())
    T.say("END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
