"""Temps de reception de la scene (invite) : connexion, transfert, ouverture, textures."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402

T.ROLE = "client"
session = T.session
compat = sys.modules[session.__name__.rsplit(".", 1)[0] + ".compat"]
filecache = sys.modules[session.__name__.rsplit(".", 1)[0] + ".filecache"]
if os.environ.get("MP_J_NOHINT"):
    # Simule deux PC : les fichiers de l'hote n'existent pas au meme chemin chez l'invite.
    _find = filecache.find
    filecache.find = lambda root, sha, size, fn, path_hint="": _find(root, sha, size, fn, "")
times = T.RESULTS.setdefault("times", {})
try:
    _open = compat.open_file

    def open_file(path):
        t = time.time()
        r = _open(path)
        times["open_blend"] = round(time.time() - t, 2)
        return r
    compat.open_file = open_file
    _extras = session.ClientSession._apply_snapshot_extras

    def extras(self, ext, binary, offset):
        t = time.time()
        r = _extras(self, ext, binary, offset)
        times["textures"] = round(time.time() - t, 2)
        return r
    session.ClientSession._apply_snapshot_extras = extras
    _on = session.ClientSession._on_snapshot

    def on_snapshot(self, msg, binary):
        times["received_at"] = time.time()
        t = time.time()
        _on(self, msg, binary)
        times["apply_total"] = round(time.time() - t, 2)
        times["mb"] = round(len(binary) / 1e6, 1)
    session.ClientSession._on_snapshot = on_snapshot

    s = None
    T.wait_for(lambda: os.path.exists(os.path.join(T.OUT, "host_ready.flag")), 300, "hote pret")
    t0 = time.time()
    if T.ONLINE:
        T.wait_for(lambda: os.path.exists(T.INVITE_FILE), 120, "lien d'invitation")
        link = T.mp.invite.parse(open(T.INVITE_FILE, encoding="utf-8").read())
        # force le passage par Internet (comme deux PC sur des reseaux differents)
        addrs = [a for a in link["addresses"] if not T.mp.invite.is_private(a[0])]
        t0 = time.time()
        s = session.join_any(addrs, link["password"], username="Client")
    else:
        for _ in range(80):
            try:
                t0 = time.time()
                s = session.join("127.0.0.1", T.PORT, "secret", username="Client")
                break
            except OSError:
                time.sleep(0.5)
    first_byte = [None]
    T.wait_for(lambda: (first_byte.__setitem__(0, first_byte[0] or (time.time() if s.conn.rx_expected > 1e6 else None)) or s.active), 600, "sync")
    t_end = time.time()
    times["total"] = round(t_end - t0, 2)
    if first_byte[0]:
        times["wait_before_transfer"] = round(first_byte[0] - t0, 2)
        times["transfer"] = round(times["received_at"] - first_byte[0], 2)
        times["mbps"] = round(times["mb"] / max(times["transfer"], 0.01), 1)
    T.log("TEMPS", times)
    T.check("scene recue", s.active)
    t_tex = time.time()
    T.wait_for(lambda: not s.incoming, 600, "textures")
    times["textures_after_scene"] = round(time.time() - t_tex, 2)
    times["total_with_textures"] = round(time.time() - t0, 2)
    T.log("TEMPS", times)
    if os.environ.get("MP_J_REJOIN"):
        first = dict(times)
        session.leave()
        T.pump(0.5)
        times.clear()
        t0 = time.time()
        s = session.join("127.0.0.1", T.PORT, "secret", username="Client2")
        T.wait_for(lambda: s.active, 600, "sync 2")
        T.wait_for(lambda: not s.incoming, 600, "textures 2")
        times["total"] = round(time.time() - t0, 2)
        T.log("TEMPS2", times)
        T.RESULTS["times_first"] = first
        T.check("2e connexion : textures deja la, rien a retelecharger", s.stream_total == 0,
                s.stream_total)
    imgs = [i for i in __import__("bpy").data.images if i.source == "FILE"]
    missing = [i.name for i in imgs if not os.path.isfile(__import__("bpy").path.abspath(i.filepath))]
    T.check("toutes les textures trouvees", not missing, missing[:5])
    T.say("END")
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
T.finish()
if T.session.current is not None:
    T.session.current.stop()
