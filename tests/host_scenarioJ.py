"""Temps de reception de la scene (hote) : chronometre la preparation de l'instantane."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

T.ROLE = "host"
session = T.session
net = sys.modules[session.__name__.rsplit(".", 1)[0] + ".net"]
times = T.RESULTS.setdefault("times", {})
try:
    _enc = net.encode

    def encode(msg, binary=b""):
        t = time.time()
        r = _enc(msg, binary)
        if msg.get("type") == "snapshot":
            times["encode"] = round(time.time() - t, 2)
            times["wire_mb"] = round(len(r[1]) / 1e6, 1)
            times["encode_done_at"] = time.time()
        return r
    net.encode = encode
    _snap = session.HostSession._send_snapshot

    def send_snapshot(self, cid):
        t = time.time()
        _snap(self, cid)
        times["prepare"] = round(time.time() - t, 2)
        times["prepare_done_at"] = time.time()
    session.HostSession._send_snapshot = send_snapshot
    s = session.host(T.PORT, "secret", "Chrono", username="Hote", online=T.ONLINE)
    if T.ONLINE:
        T.wait_for(lambda: s.public_address(), 60, "adresse Internet")
        with open(T.INVITE_FILE + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(s.invite_link())
        os.replace(T.INVITE_FILE + ".tmp", T.INVITE_FILE)
    T.pump(1.0)
    open(os.path.join(T.OUT, "host_ready.flag"), "w").close()
    T.wait_for(lambda: len(s.users) == 2, 300, "client")
    T.barrier("END", 400)
    with open(os.path.join(T.OUT, "times_host.json"), "w") as fh:
        json.dump(times, fh)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
