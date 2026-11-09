"""Scenario M (invite) : voir m_common."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import m_common as M  # noqa: E402

T.ROLE = "client"
try:
    T.wait_for(lambda: os.path.exists(os.path.join(T.OUT, "host_ready.flag")), 300, "hote pret")
    s = None
    if T.ONLINE:
        import time
        T.wait_for(lambda: os.path.exists(T.INVITE_FILE), 300, "lien d'invitation")
        link = T.mp.invite.parse(open(T.INVITE_FILE, encoding="utf-8").read())
        addrs = link["addresses"]
        if T.VIA == "bore":
            addrs = [a for a in addrs if a[0] == "bore.pub"]
        elif T.VIA == "web":
            addrs = [a for a in addrs if a[0].startswith("wss://")]
        elif T.VIA == "all":
            # comme un invite sur un autre reseau : les adresses locales ne repondent pas
            addrs = [("10.255.255.1", a[1]) if T.mp.invite.is_private(a[0]) or a[0].startswith("100.")
                     else a for a in addrs]
        t0 = time.time()
        s = T.session.join_any(addrs, link["password"], username="Client")
        T.RESULTS["chemin"] = [s.address, s.port, round(time.time() - t0, 2)]
        T.log("CHEMIN", s.address, s.port, "%.2f s" % (time.time() - t0))
    for _ in range(80):
        if s is not None:
            break
        try:
            s = T.session.join("127.0.0.1", T.PORT, "secret", username="Client")
            break
        except OSError:
            T.pump(0.5)
    T.wait_for(lambda: s.active and not s.incoming, 300, "sync")
    phase = ["join"]
    sent = M.watch_sends(s.tracker, phase)
    T.pump(5.0)
    T.RESULTS["ping"] = s.rtt
    T.log("PING", s.rtt)
    phase[0] = "run"
    T.say("JOINED")
    if M.DRIVER == "client":
        M.driver(T, sent, phase)
    else:
        M.viewer(T, sent)
    T.barrier("END_H", 60)
    T.say("END_C")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
