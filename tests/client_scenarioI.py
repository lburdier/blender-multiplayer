import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import probe_util as P  # noqa: E402

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
    P.install(s)
    T.wait_for(lambda: s.active, 300, "sync")
    T.pump(1.0)
    T.say("C0")
    T.pump(3.0)
    P.ON[0] = True
    T.pump(15.0)
    P.ON[0] = False
    T.RESULTS["probe"] = P.report()
    T.log("PROBE", P.report())
    T.barrier("END", 120)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
