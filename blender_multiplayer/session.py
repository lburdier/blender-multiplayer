"""Session multijoueur : hote (autorite) et clients.

Modele :
- l'hote est l'autorite : il ordonne toutes les operations (numero de
  sequence), les valide (verrous, roles), les applique chez lui, les
  inscrit dans l'historique et les rediffuse ;
- chaque client applique ses modifications localement tout de suite (zero
  latence ressentie) puis les envoie ; tant qu'elles ne sont pas confirmees
  (ack), les changements distants sur les MEMES champs sont ignores, ce qui
  garantit que tout le monde converge vers l'ordre choisi par l'hote ;
- a l'arrivee, un client recoit un instantane complet (.blend) de la scene.
"""

import collections
import datetime
import errno
import gc
import hashlib
import hmac
import os
import queue
import re
import secrets
import sys
import time
import traceback

import bpy

from . import (compat, filecache, history, ids, invite, live, net, payloads, presence, serialize, smooth,
               tunnel)
from . import tracker as tracker_mod

current = None
last_reason = ""          # affiche dans le panneau apres la fin d'une session
_SILENT_REASONS = {"", "depart", "nouvelle session", "arret par l'hote", "addon desactive"}

PALETTE = [(0.95, 0.35, 0.25), (0.25, 0.65, 0.98), (0.35, 0.85, 0.40), (0.98, 0.78, 0.20),
           (0.75, 0.45, 0.98), (0.20, 0.85, 0.85), (0.98, 0.45, 0.72), (0.65, 0.65, 0.65)]

ADDON_VERSION = compat.ADDON_VERSION_STR


def file_family(version_str):
    """4 ou 5 : Blender 5.0 a change l'en-tete des fichiers .blend, illisibles par 4.x
    (et les blocs envoyes par un 5.x sont illisibles par un hote 4.x)."""
    try:
        return 5 if int(str(version_str).split(".")[0]) >= 5 else 4
    except ValueError:
        return 0


def _prefs():
    from . import prefs
    return prefs.get()


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_") or "session"


def root_dir():
    p = _prefs()
    root = bpy.path.abspath(p.root_dir) if p.root_dir else os.path.join(
        os.path.expanduser("~"), "BlenderMultiplayer")
    os.makedirs(root, exist_ok=True)
    return root


def _auth(password, nonce):
    return hmac.new(password.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def _timer():
    s = current
    if s is None:
        return None
    return s.tick()


class BaseSession:
    is_host = False

    def __init__(self, username, color):
        self.username = username
        self.color = tuple(color)
        self.inbox = queue.Queue()
        self.store = payloads.PayloadStore()
        self.tracker = tracker_mod.Tracker(self)
        self.users = {}
        self.me = None
        self.role = "editor"
        self.active = False
        self.state = "init"
        self.chat = collections.deque(maxlen=300)
        self.toasts = collections.deque(maxlen=40)
        self.hist = collections.OrderedDict()
        self.locks = {}
        self.pings = collections.deque(maxlen=20)
        self.follow = None
        self.follow_state = {}
        self.my_locks = set()
        self.last_presence = None
        self.last_presence_t = 0.0
        self.session_name = ""
        self.session_dir = ""
        self.logs = collections.deque(maxlen=400)
        self._log_seen = {}
        self.started = time.time()
        self.reconcile_pending = False
        self.loading = False
        self.sync_frame = False
        self.last_change_t = 0.0
        self.local_backup_t = time.time()
        self.closed_reason = ""
        self.error = ""

    # ------------------------------------------------------------------
    def log(self, text):
        # Evite d'inonder la console avec la meme erreur a chaque tick.
        now = time.time()
        key = text[:160]
        last = self._log_seen.get(key)
        if last is not None and now - last < 30.0:
            return
        self._log_seen[key] = now
        if len(self._log_seen) > 500:
            self._log_seen.clear()
        self.logs.append((now, text))
        print("[Multiplayer] %s" % text)

    def toast(self, text, color=(1.0, 1.0, 1.0)):
        self.toasts.append({"t": time.time(), "text": text, "c": tuple(color)})
        compat.tag_redraw(("VIEW_3D",))

    def assets_dir(self):
        path = os.path.join(self.session_dir or compat.temp_dir(), "assets")
        os.makedirs(path, exist_ok=True)
        return path

    def backups_dir(self):
        path = os.path.join(self.session_dir or root_dir(), "backups")
        os.makedirs(path, exist_ok=True)
        return path

    def status_line(self):
        return ""

    def user_name(self, uid):
        u = self.users.get(uid)
        return u.get("name", "?") if u else "?"

    def history_items(self):
        return list(self.hist.values())

    def _update_hist(self, summary):
        if summary:
            self.hist[summary["s"]] = summary
            while len(self.hist) > 400:
                self.hist.popitem(last=False)

    def _start_timer(self):
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=0.05, persistent=True)

    # ------------------------------------------------------------------
    def tick(self):
        now = time.time()
        try:
            tr = getattr(self, "tracker", None)
            if tr is not None and tr.smoother.items:
                tr.smoother.step(now)   # en plus du minuteur a 60 i/s (filet de securite)
            self._tick(now)
        except Exception:  # noqa: BLE001
            self.log("erreur: " + traceback.format_exc())
        if current is not self:
            return None
        if not self.active:
            return 0.1
        # Cadence adaptative : 60 Hz pendant qu'un objet bouge (chez soi ou chez
        # les autres), 30 Hz sinon.
        tr = getattr(self, "tracker", None)
        busy = (tr is not None and bool(tr.smoother.items)) or now - self.last_change_t < 0.6
        return 0.016 if busy else 0.03

    def _tick(self, now):
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Natif : Ctrl+Z hors addon, chargement de fichier
    # ------------------------------------------------------------------
    def on_native_undo(self):
        if self.active and not self.loading:
            self.reconcile_pending = True
            self.tracker.stale = True

    def _reconcile(self):
        self.reconcile_pending = False
        keep = set(self.my_locks)
        n = self.tracker.restore_known(keep=keep)
        for uid in keep:
            self.tracker.dirty.setdefault(uid, set()).update(("x", "g"))
        if n:
            self.toast("Annulation native ignoree en session : utilisez Ctrl+Z (annulation "
                       "multijoueur) ou l'historique.", (1.0, 0.8, 0.3))

    def on_load_post(self):
        if self.loading:
            return
        self.toast("Un autre fichier a ete ouvert : session quittee.", (1, 0.4, 0.4))
        self.stop("fichier change")

    # ------------------------------------------------------------------
    # Presence, verrous, suivi
    # ------------------------------------------------------------------
    def _presence_tick(self, now):
        if now - self.last_presence_t < 0.1:
            return
        try:
            pres = presence.capture(self)
        except Exception as exc:  # noqa: BLE001
            self.log("presence: %s" % exc)
            return
        pres["rtt"] = getattr(self, "rtt", 0)
        self.my_locks = set(pres.get("edit", []))
        if pres != self.last_presence or now - self.last_presence_t > 2.0:
            self.last_presence = pres
            self.last_presence_t = now
            if self.me in self.users:
                self.users[self.me]["presence"] = pres
            self.send_presence(pres)
        if self.follow is not None:
            target = self.users.get(self.follow)
            if target is None or not presence.apply_follow(target.get("presence"), self.follow_state):
                if target is not None:
                    self.toast("Suivi de %s arrete." % target.get("name", "?"))
                self.follow = None
                self.follow_state = {}

    def _enforce_locks(self):
        for uid in self.my_locks:
            owner = self.locks.get(uid)
            if owner is not None and owner != self.me:
                self.toast("%s modifie deja cet objet : retour en mode Objet." %
                           self.user_name(owner), (1, 0.5, 0.3))
                self.exit_mode()
                break

    def exit_mode(self):
        try:
            compat.call_op(bpy.ops.object.mode_set, view3d=True, mode="OBJECT")
        except Exception as exc:  # noqa: BLE001
            self.log("sortie de mode: %s" % exc)
        self.my_locks = set()
        self.tracker.mode_scan_t = 0.0

    def start_follow(self, uid):
        if uid == self.me or uid not in self.users:
            return
        self.follow = uid
        self.follow_state = {}

    def send_presence(self, pres):
        raise NotImplementedError

    def _local_backup_tick(self, now):
        p = _prefs()
        if not p.local_backup or p.autosave_minutes <= 0:
            return
        if now - self.local_backup_t < p.autosave_minutes * 60:
            return
        if now - self.last_change_t < 1.5:
            return
        self.local_backup_t = now
        try:
            folder = os.path.join(root_dir(), "local_backups")
            path = os.path.join(folder, "%s_%s.blend" % (_safe(self.session_name), _stamp()))
            compat.save_copy(path)
            _rotate(folder, p.max_backups)
        except Exception as exc:  # noqa: BLE001
            self.log("sauvegarde locale: %s" % exc)

    def stop(self, reason=""):
        global current, last_reason
        try:
            smooth.stop_all()
        except Exception:  # noqa: BLE001
            pass
        self.active = False
        self.state = "closed"
        self.closed_reason = reason
        if current is self or current is None:
            last_reason = "" if reason in _SILENT_REASONS else "Session terminee : %s" % reason
        if current is self:
            current = None
        compat.tag_redraw()


def _rotate(folder, keep):
    try:
        files = sorted((f for f in os.listdir(folder) if f.endswith(".blend")),
                       key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    except OSError:
        return
    for f in files[:-max(1, keep)]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass


# ===========================================================================
# Hote
# ===========================================================================

class HostSession(BaseSession):
    is_host = True

    def __init__(self, username, color):
        super().__init__(username, color)
        self.server = None
        self.clients = {}
        self.password = ""
        self.port = 0
        self.msg_seq = 0
        self.next_user = 2
        self.history = None
        self.tunnel = None
        self.password_generated = False
        self._tunnel_seen = (None, 0)
        self.autosave_t = time.time()
        self.autosave_seq = 0
        self.flush_t = time.time()

    def start(self, port, password, session_name, sync_frame=False, online=False, relay=""):
        global current
        if online and not password:
            # Joignable depuis Internet : jamais sans mot de passe.
            password = secrets.token_urlsafe(6)
            self.password_generated = True
        self.session_name = session_name or "Session"
        self.session_dir = os.path.join(root_dir(), "sessions",
                                        "%s_%s" % (_safe(self.session_name), _stamp()))
        os.makedirs(self.session_dir, exist_ok=True)
        self.store = payloads.PayloadStore(disk_dir=os.path.join(self.session_dir, "payloads"))
        self.tracker = tracker_mod.Tracker(self)
        self.sync_frame = sync_frame
        self.tracker.sync_frame = sync_frame
        self.history = history.History(self.session_dir, self.store)
        self.password = password or ""
        self.requested_port = int(port)
        self.server, self.port = open_server(self.requested_port, self.inbox)
        if online:
            self.tunnel = tunnel.Tunnel(self.port, relay or tunnel.DEFAULT_RELAY)
        self.tracker.init_known(write_blobs=True)
        try:
            compat.save_copy(os.path.join(self.session_dir, "checkpoints", "00_depart.blend"))
        except Exception as exc:  # noqa: BLE001
            self.log("point de depart: %s" % exc)
        self.me = 1
        self.users = {1: {"id": 1, "name": self.username, "color": self.color, "role": "host",
                          "ver": compat.VERSION_STR, "presence": {}}}
        self.active = True
        self.state = "ready"
        current = self
        self._start_timer()
        entry, _ = self.history.add(self.me, self.username, self.color, [],
                                    "Debut de la session", kind="checkpoint", mergeable=False)
        entry["file"] = os.path.join(self.session_dir, "checkpoints", "00_depart.blend")
        self._update_hist(self.history.summary(entry))
        if self.port != self.requested_port:
            self.toast("Port %d deja utilise sur ce PC : session ouverte sur le port %d."
                       % (self.requested_port, self.port), (1, 0.8, 0.3))
        else:
            self.toast("Session \"%s\" ouverte sur le port %d." % (self.session_name, self.port),
                       (0.5, 1, 0.5))
        self.log("hote demarre: %s port %d (%s)" % (self.session_name, self.port,
                                                    ", ".join(net.local_addresses())))
        return self

    def status_line(self):
        return "Multijoueur - Hote \"%s\" - %d joueur(s) - %s" % (
            self.session_name, len(self.users), self.public_address() or "port %d" % self.port)

    def public_address(self):
        return self.tunnel.public_address if self.tunnel is not None else ""

    def invite_addresses(self):
        """Adresses a essayer par l'invite : reseau local principal (delai court),
        puis Internet, puis les autres adresses locales."""
        lan = [(ip, self.port) for ip in net.local_addresses() if not ip.startswith("127.")]
        out = lan[:1]
        pub = self.public_address()
        if pub:
            host, _, port = pub.rpartition(":")
            out.append((host, int(port)))
        out.extend(lan[1:3])
        return out

    def invite_link(self):
        return invite.make(self.session_name, self.invite_addresses(), self.password)

    def _tunnel_tick(self):
        t = self.tunnel
        if t is None:
            return
        seen = (t.state, t.generation)
        if seen == self._tunnel_seen:
            return
        prev_state, prev_gen = self._tunnel_seen
        self._tunnel_seen = seen
        if t.state == "open":
            if prev_gen and t.generation != prev_gen and t.public_address:
                self.toast("Adresse Internet retablie : %s" % t.public_address, (0.5, 1, 0.5))
            else:
                self.toast("Session joignable par Internet : %s" % t.public_address, (0.5, 1, 0.5))
            self.log("tunnel ouvert: %s" % t.public_address)
            compat.tag_redraw()
        elif t.state == "retry" and prev_state in (None, "connecting", "open"):
            self.toast("Adresse Internet indisponible (%s), nouvel essai..." % t.error, (1, 0.7, 0.3))
            self.log("tunnel: %s" % t.error)
            compat.tag_redraw()

    # ------------------------------------------------------------------
    def _ready_conns(self, exclude=None):
        for cid, c in self.clients.items():
            if c.get("ready") and cid != exclude:
                conn = self.server.get(cid)
                if conn is not None and conn.alive:
                    yield cid, conn

    def broadcast(self, msg, binary=b"", exclude=None):
        for _cid, conn in self._ready_conns(exclude):
            conn.send(msg, binary)

    def _users_msg(self):
        return {"type": "users", "users": [
            {k: v for k, v in u.items() if k != "presence"} for u in self.users.values()]}

    def send_presence(self, pres):
        self.broadcast({"type": "presence", "u": self.me, "p": pres})
        self._recompute_locks()

    def send_live(self, msg, binary=b""):
        self.broadcast(msg, binary)

    def live_backlog(self):
        return max((conn.queued for _cid, conn in self._ready_conns()), default=0)

    # ------------------------------------------------------------------
    def _tick(self, now):
        if self.reconcile_pending:
            self._reconcile()
        self._process_inbox(now)
        if not self.active:
            return
        self.tracker.live.flush(now)
        self.tracker.live.tick(now)
        self.tracker.poll_modes(now, set())
        changes = self.tracker.collect(now)
        if changes:
            label = history.describe(changes, self.tracker.operator_label(), self.tracker.known)
            self.commit(self.me, changes, label, local=True)
            self.last_change_t = now
        self._presence_tick(now)
        self._enforce_locks()
        self._autosave_tick(now)
        self._tunnel_tick()
        self._expire_tick(now)
        if now - self.flush_t > 3.0:
            self.flush_t = now
            if self.history is not None and self.history._unflushed is not None \
                    and now - self.history._unflushed["time"] > 2.0:
                self.history.flush()
        for cid, c in list(self.clients.items()):
            conn = self.server.get(cid)
            if conn is not None and conn.alive and now - conn.last_rx > 60 and conn.rx_expected == 0:
                self.log("joueur %s sans reponse" % cid)
                conn.close("delai depasse")

    def _process_inbox(self, now):
        deadline = now + 0.05
        while time.time() < deadline:
            try:
                cid, msg, binary = self.inbox.get_nowait()
            except queue.Empty:
                break
            if msg is None:
                self._on_disconnect(cid, binary)
                continue
            try:
                self._handle(cid, msg, binary)
            except Exception:  # noqa: BLE001
                self.log("message %s: %s" % (msg.get("type"), traceback.format_exc()))

    def _handle(self, cid, msg, binary):
        t = msg.get("type")
        conn = self.server.get(cid)
        if t == "__connect__":
            nonce = os.urandom(12).hex()
            self.clients[cid] = {"user": None, "nonce": nonce, "ready": False}
            if conn:
                conn.send({"type": "challenge", "nonce": nonce, "proto": net.PROTOCOL_VERSION,
                           "name": self.session_name, "pw": bool(self.password)})
            return
        client = self.clients.get(cid)
        if client is None or conn is None:
            return
        if t == "hello":
            self._on_hello(cid, conn, client, msg)
            return
        uid = client.get("user")
        if uid is None:
            return
        user = self.users.get(uid)
        if user is None:
            return
        if binary:
            payloads.detach(msg, binary, self.store)
        if t == "op":
            self.commit(uid, msg.get("ch", []), msg.get("l"), origin=cid, op_id=msg.get("id"))
        elif t == "presence":
            user["presence"] = msg.get("p", {})
            self.broadcast({"type": "presence", "u": uid, "p": user["presence"]}, exclude=cid)
            self._recompute_locks()
        elif t == "live":
            owner = self.locks.get(msg.get("u"))
            if user.get("role") != "viewer" and (owner is None or owner == uid):
                self.tracker.live.receive(msg, binary)
                for _cid, other in self._ready_conns(cid):
                    if other.queued < live.MAX_QUEUED:
                        other.send(msg, binary)
        elif t == "chat":
            self.post_chat(uid, msg.get("text", ""))
        elif t == "mark":
            self.post_mark(uid, msg.get("loc"))
        elif t == "undo":
            self.request_undo(uid)
        elif t == "redo":
            self.request_redo(uid)
        elif t == "hist_undo":
            self.undo_entry(uid, int(msg.get("seq", 0)))
        elif t == "hist_revert":
            self.revert_to(uid, int(msg.get("seq", 0)))
        elif t == "fetch":
            self._send_state(conn, msg.get("uids", []))
        elif t == "resync":
            self._send_snapshot(cid)
        elif t == "have":
            client["have"] = set(str(x) for x in (msg.get("sha") or [])[:20000])
            if not client.get("ready"):
                self._send_snapshot(cid)
                conn.send(self._users_msg())
        elif t == "ping":
            conn.send({"type": "pong", "t": msg.get("t")})
        elif t == "bye":
            client["left"] = True
            conn.close("depart")

    def _on_hello(self, cid, conn, client, msg):
        if msg.get("proto") != net.PROTOCOL_VERSION:
            conn.send({"type": "kick", "reason": "Version du protocole incompatible : mettez a jour "
                       "l'addon (hote %s)." % ADDON_VERSION})
            conn.close_after_flush()
            return
        if self.password and msg.get("auth") != _auth(self.password, client["nonce"]):
            conn.send({"type": "kick", "reason": "Mot de passe incorrect."})
            conn.close_after_flush()
            return
        cver = str(msg.get("ver", ""))
        if file_family(cver) != file_family(compat.VERSION_STR):
            conn.send({"type": "kick", "reason": (
                "Versions de Blender incompatibles : l'hote utilise Blender %s, vous Blender %s. "
                "Les fichiers de Blender 4.x et de Blender 5.x ne se lisent pas entre eux : "
                "utilisez tous Blender 5.0 ou plus (ou tous Blender 4.x)." % (compat.VERSION_STR, cver))})
            conn.close_after_flush()
            self.log("joueur refuse : Blender %s (hote %s)" % (cver, compat.VERSION_STR))
            return
        name = (msg.get("name") or "Joueur").strip()[:32] or "Joueur"
        taken = {u["name"] for u in self.users.values()}
        base, n = name, 2
        while name in taken:
            name = "%s (%d)" % (base, n)
            n += 1
        uid = self.next_user
        self.next_user += 1
        color = msg.get("color") or PALETTE[(uid - 1) % len(PALETTE)]
        if any(tuple(u["color"]) == tuple(color) for u in self.users.values()):
            color = PALETTE[(uid - 1) % len(PALETTE)]
        role = "viewer" if msg.get("viewer") else "editor"
        self.users[uid] = {"id": uid, "name": name, "color": tuple(color), "role": role,
                           "ver": msg.get("ver", "?"), "presence": {}}
        client["user"] = uid
        client["caps"] = set(str(c) for c in (msg.get("caps") or ())[:20])
        conn.send({"type": "welcome", "user": uid, "session": self.session_name, "live": True,
                   "settings": {"sync_frame": self.sync_frame}, "host_ver": compat.VERSION_STR,
                   "hist": self.history.summaries(), "locks": self.locks,
                   "chat": list(self.chat)[-50:]})
        if "manifest" in (msg.get("caps") or ()):
            # L'invite dira d'abord quelles textures il a deja : seules les autres seront envoyees.
            self._send_manifest(cid, conn)
        else:
            self._send_snapshot(cid)
        self.broadcast(self._users_msg())
        for ouid, ouser in self.users.items():
            if ouid != uid and ouser.get("presence"):
                conn.send({"type": "presence", "u": ouid, "p": ouser["presence"]})
        self.post_system("%s a rejoint la session." % name)
        if msg.get("ver") and msg.get("ver") != compat.VERSION_STR:
            warn = ("Attention : %s utilise Blender %s et l'hote Blender %s. Utilisez la meme "
                    "version pour une fidelite parfaite." % (name, msg.get("ver"), compat.VERSION_STR))
            self.post_system(warn)

    def _send_snapshot(self, cid):
        conn = self.server.get(cid)
        client = self.clients.get(cid)
        if conn is None or client is None:
            return
        self.tracker.smoother.finish_all()
        changes = self.tracker.collect(full=True)
        if changes:
            self.commit(self.me, changes, history.describe(changes, None, self.tracker.known),
                        local=True)
        path = os.path.join(compat.temp_dir(), "snapshot_%d.blend" % cid)
        compat.save_copy(path)
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            os.remove(path)
        except OSError:
            pass
        ext, parts, later = self._snapshot_extras(client.get("have", ()),
                                                  stream="stream" in client.get("caps", ()))
        extra = sum(len(p) for p in parts)
        conn.send({"type": "snapshot", "seq": self.msg_seq, "name": self.session_name,
                   "size": len(data) + extra, "blend_len": len(data), "ext": ext},
                  data + b"".join(parts) if parts else data)
        client["ready"] = True
        for sha, path, fn in later:
            self._stream_file(conn, sha, path, fn)
        if later:
            self.log("%d texture(s) envoyee(s) apres la scene (%d Ko)" % (
                len(later), sum(e.get("size", 0) for e in ext if e.get("later")) // 1024))
        reused = sum(1 for e in ext if e.get("have"))
        inline = sum(1 for e in ext if not e.get("have") and not e.get("later"))
        self.log("instantane envoye au client %s : scene %d Ko + %d fichier(s)/texture(s) %d Ko"
                 "%s" % (cid, len(data) // 1024, inline, extra // 1024,
                         " (%d deja chez l'invite)" % reused if reused else ""))

    def _external_file_list(self):
        """[(uid, attr, data-block, chemin, tuile)] des fichiers externes a joindre a la scene."""
        out = []
        for attr in serialize.FILE_ATTRS:
            for idb in getattr(bpy.data, attr, ()):
                uid = ids.get_uid(idb)
                if not uid:
                    continue
                for path, tile in serialize.external_files(idb):
                    out.append((uid, attr, idb, path, tile))
        return out

    def _send_manifest(self, cid, conn):
        files, seen = [], set()
        for _uid, _attr, _idb, path, tile in self._external_file_list():
            if tile is not None:
                continue
            try:
                size = os.path.getsize(path)
                if size > serialize.MAX_ATTACHED_FILE:
                    continue
                sha = filecache.file_sha(path)
            except OSError:
                continue
            if sha in seen:
                continue
            seen.add(sha)
            files.append({"sha": sha, "size": size, "fn": os.path.basename(path), "path": path})
        conn.send({"type": "manifest", "files": files})

    def _stream_file(self, conn, sha, path, fn):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self.log("texture %s: %s" % (fn, exc))
            return
        total = len(data)
        for off in range(0, max(total, 1), STREAM_CHUNK):
            conn.send_bulk({"type": "filechunk", "sha": sha, "fn": fn, "off": off, "total": total},
                           data[off:off + STREAM_CHUNK])

    def _snapshot_extras(self, have=(), stream=False):
        """Ce que le .blend ne contient pas : fichiers externes (textures, tuiles
        UDIM, polices, sons, videos, VDB, Alembic), bibliotheques liees et pixels
        des textures peintes non enregistrees. Les fichiers que l'invite possede
        deja (`have` : empreintes sha256) ne sont pas renvoyes."""
        ext, parts, later = [], [], []
        budget = [2 * 1024 ** 3]
        files = self._external_file_list()
        hashes = {}
        for _uid, _attr, _idb, path, tile in files:
            if tile is None:
                try:
                    hashes[path] = (filecache.file_sha(path), os.path.getsize(path))
                except OSError:
                    pass
        missing = {h for h in hashes.values() if h[0] not in have}
        # Beaucoup de textures a envoyer : la scene part d'abord, les textures ensuite.
        stream = stream and sum(m[1] for m in missing) > STREAM_MIN
        streamed = set()

        def add(item, blob):
            if len(blob) > budget[0]:
                return
            budget[0] -= len(blob)
            item["len"] = len(blob)
            ext.append(item)
            parts.append(blob)

        def read(path):
            try:
                if os.path.getsize(path) <= serialize.MAX_ATTACHED_FILE:
                    with open(path, "rb") as fh:
                        return fh.read()
            except OSError:
                pass
            return None

        for uid, attr, idb, path, tile in files:
            item = {"k": "file", "u": uid, "a": attr, "fn": os.path.basename(path)}
            if tile is None and path in hashes:
                sha, size = hashes[path]
                item["sha"] = sha
                if sha in have:
                    item.update(len=0, have=1)
                    ext.append(item)
                    continue
                if stream and size <= serialize.MAX_ATTACHED_FILE:
                    item.update(len=0, later=1, size=size)
                    ext.append(item)
                    if sha not in streamed:
                        streamed.add(sha)
                        later.append((sha, path, item["fn"]))
                    continue
            blob = read(path)
            if blob is not None:
                if tile is not None:
                    item["tile"] = tile
                    item["pat"] = os.path.basename(bpy.path.abspath(idb.filepath))
                add(item, blob)
        for lib in bpy.data.libraries:
            try:
                path = bpy.path.abspath(lib.filepath)
            except Exception:  # noqa: BLE001
                continue
            blob = read(path) if os.path.isfile(path) else None
            if blob is not None:
                add({"k": "lib", "n": lib.name, "fn": os.path.basename(path)}, blob)
        for img in bpy.data.images:
            uid = ids.get_uid(img)
            try:
                if uid and img.is_dirty and serialize.pixels_supported(img):
                    blob, meta = serialize.image_pixels(img)
                    meta.update(k="px", u=uid)
                    add(meta, blob)
            except Exception as exc:  # noqa: BLE001
                self.log("pixels %s: %s" % (img.name, exc))
        return ext, parts, later

    def _send_state(self, conn, uids):
        states = {}
        for uid in uids[:500]:
            k = self.tracker.known.get(uid)
            states[uid] = {"t": k["t"], "f": k["f"]} if k else None
        msg = {"type": "state", "s": states}
        binary = payloads.attach(msg, self.store)
        conn.send(msg, binary)

    def _on_disconnect(self, cid, reason):
        client = self.clients.pop(cid, None)
        self.server.drop(cid)
        if client is None or client.get("user") is None:
            return
        uid = client["user"]
        user = self.users.pop(uid, None)
        if self.follow == uid:
            self.follow = None
        self._recompute_locks()
        self.broadcast(self._users_msg())
        if user:
            if client.get("left"):
                self.post_system("%s a quitte la session." % user["name"])
            else:
                self.post_system("%s a ete deconnecte (%s)." % (user["name"], reason))

    # ------------------------------------------------------------------
    # Validation + application + historique + diffusion
    # ------------------------------------------------------------------
    def commit(self, user_id, changes, label=None, origin=None, op_id=None, local=False,
               kind="op", ref=None, mergeable=True):
        user = self.users.get(user_id, {})
        known = self.tracker.known
        accepted, rejects = [], []
        for c in changes:
            uid = c["u"]
            owner = self.locks.get(uid)
            if owner is not None and owner != user_id:
                rejects.append(uid)
                continue
            if user.get("role") == "viewer" and kind == "op":
                rejects.append(uid)
                continue
            if not local:
                if c["k"] == "u" and uid not in known:
                    rejects.append(uid)
                    continue
                if c["k"] == "c" and uid in known:
                    c = dict(c, k="u")
                if c["k"] == "d" and uid not in known:
                    continue
            accepted.append(c)
        before = []
        for c in accepted:
            kn = known.get(c["u"])
            if c["k"] == "u":
                before.append({k: kn["f"].get(k) for k in c["f"]} if kn else {})
            elif c["k"] == "d":
                before.append(dict(kn["f"]) if kn else None)
            else:
                before.append(None)
        if local:
            self.tracker.mark_sent(accepted)
            if rejects:
                self.tracker.restore_known(uids=rejects)
                self.toast("Modification refusee : objet en cours d'edition par un autre joueur.",
                           (1, 0.5, 0.3))
        else:
            errors = self.tracker.apply_remote(accepted)
            if errors:
                bad = {u for u, _e in errors}
                keep = [(c, b) for c, b in zip(accepted, before) if c["u"] not in bad]
                accepted = [c for c, _b in keep]
                before = [b for _c, b in keep]
                rejects.extend(bad)
        fixes = {}
        hist_changes = []
        for c, b in zip(accepted, before):
            kn = known.get(c["u"])
            if c["k"] == "d":
                a = None
            elif c["k"] == "c":
                a = dict(kn["f"]) if kn else dict(c["f"])
            else:
                a = {k: kn["f"].get(k) for k in c["f"]} if kn else dict(c["f"])
                if kn and "blob" in kn["f"] and "fp" in a:
                    a["blob"] = kn["f"]["blob"]
            if kn and c["k"] != "d" and "name" in c.get("f", {}) and kn["f"].get("name") != c["f"]["name"]:
                fixes[c["u"]] = {"name": kn["f"]["name"]}
                c["f"] = dict(c["f"], name=kn["f"]["name"])
            name = c.get("n") or (kn["f"].get("name") if kn else (b or {}).get("name", ""))
            hist_changes.append({"u": c["u"], "t": c["t"], "k": c["k"], "b": b, "a": a, "n": name})
        summary = None
        if accepted:
            self.msg_seq += 1
            if not label:
                label = history.describe(accepted, None, known)
            entry, _merged = self.history.add(user_id, user.get("name", "?"),
                                              user.get("color", (1, 1, 1)), hist_changes, label,
                                              kind=kind, ref=ref, mergeable=mergeable)
            summary = self.history.summary(entry)
            self._update_hist(summary)
            msg = {"type": "op", "seq": self.msg_seq, "u": user_id, "ch": accepted, "h": summary}
            binary = payloads.attach(msg, self.store)
            self.broadcast(msg, binary, exclude=origin)
            if not local:
                compat.tag_redraw()
        if origin is not None:
            conn = self.server.get(origin)
            if conn is not None:
                rej = {}
                for uid in rejects:
                    k = known.get(uid)
                    rej[uid] = {"t": k["t"], "f": k["f"]} if k else None
                ack = {"type": "ack", "id": op_id, "seq": self.msg_seq, "fix": fixes, "rej": rej,
                       "h": summary}
                binary = payloads.attach(ack, self.store) if rej else b""
                conn.send(ack, binary)
        return accepted

    # ------------------------------------------------------------------
    # Historique : annuler / retablir / revenir
    # ------------------------------------------------------------------
    def _notify_user(self, user_id, text, color=(1, 0.8, 0.3)):
        if user_id == self.me:
            self.toast(text, color)
            return
        for cid, c in self.clients.items():
            if c.get("user") == user_id:
                conn = self.server.get(cid)
                if conn:
                    conn.send({"type": "notice", "text": text, "c": color})

    def _hist_changed(self, entry):
        s = self.history.summary(entry)
        self._update_hist(s)
        self.broadcast({"type": "hist", "h": [s]})

    def request_undo(self, user_id):
        self._flush_local()
        e = self.history.last_undoable(user_id)
        if e is None:
            self._notify_user(user_id, "Rien a annuler.")
            return
        self._undo(user_id, e)

    def _undo(self, user_id, e):
        changes, skipped = self.history.inverse(e, self.tracker.known)
        self.commit(user_id, changes, "Annuler : " + e["label"], kind="undo", ref=e["seq"],
                    mergeable=False)
        e["undone"] = True
        for seq in e.get("marked", []):
            m = self.history.by_seq.get(seq)
            if m is not None:
                m["undone"] = False
                self._hist_changed(m)
        self.history.push_redo(user_id, e["seq"])
        self._hist_changed(e)
        if skipped:
            self._notify_user(user_id, "%d propriete(s) conservee(s) : modifiee(s) entre-temps "
                                       "par un autre joueur." % skipped)

    def request_redo(self, user_id):
        self._flush_local()
        e = self.history.pop_redo(user_id)
        if e is None:
            self._notify_user(user_id, "Rien a retablir.")
            return
        changes, skipped = self.history.forward(e, self.tracker.known)
        self.commit(user_id, changes, "Retablir : " + e["label"], kind="redo", ref=e["seq"],
                    mergeable=False)
        e["undone"] = False
        self._hist_changed(e)
        if skipped:
            self._notify_user(user_id, "%d propriete(s) non retablie(s) : modifiee(s) entre-temps."
                              % skipped)

    def undo_entry(self, user_id, seq):
        self._flush_local()
        e = self.history.by_seq.get(seq)
        if e is None or e["undone"] or not e["changes"]:
            self._notify_user(user_id, "Cette entree ne peut pas etre annulee.")
            return
        self._undo(user_id, e)

    def revert_to(self, user_id, seq):
        self._flush_local()
        e = self.history.by_seq.get(seq)
        if e is None:
            self._notify_user(user_id, "Point d'historique introuvable.")
            return
        changes = self.history.state_at(seq, self.tracker.known)
        if not changes:
            self._notify_user(user_id, "La scene est deja dans cet etat.")
            return
        marked = [x["seq"] for x in self.history.entries
                  if x["seq"] > seq and not x["undone"] and x["kind"] in ("op", "redo", "revert")]
        self.commit(user_id, changes, "Retour au point #%d (%s)" % (seq, e["label"]),
                    kind="revert", ref=seq, mergeable=False)
        last = self.history.entries[-1]
        if last["kind"] == "revert":
            last["marked"] = marked
        for s in marked:
            m = self.history.by_seq.get(s)
            if m is not None:
                m["undone"] = True
                self._hist_changed(m)

    def _flush_local(self):
        changes = self.tracker.collect(full=True)
        if changes:
            self.commit(self.me, changes,
                        history.describe(changes, self.tracker.operator_label(), self.tracker.known),
                        local=True)

    # ------------------------------------------------------------------
    # Verrous (objets en mode Edition / Sculpt / Pose... par quelqu'un)
    # ------------------------------------------------------------------
    def _recompute_locks(self):
        claims = {}
        for uid, user in self.users.items():
            if uid == self.me:
                edit = self.my_locks
            else:
                edit = (user.get("presence") or {}).get("edit", [])
            for u in edit:
                claims.setdefault(u, []).append(uid)
        locks = {}
        for u, owners in claims.items():
            prev = self.locks.get(u)
            locks[u] = prev if prev in owners else owners[0]
        if locks != self.locks:
            self.locks = locks
            self.broadcast({"type": "locks", "l": locks})
            compat.tag_redraw(("VIEW_3D",))

    # ------------------------------------------------------------------
    # Chat & pings
    # ------------------------------------------------------------------
    def post_chat(self, uid, text):
        text = (text or "").strip()[:500]
        if not text:
            return
        user = self.users.get(uid, {})
        item = {"u": uid, "n": user.get("name", "?"), "c": user.get("color", (1, 1, 1)),
                "text": text, "t": time.time()}
        self.chat.append(item)
        self.broadcast({"type": "chat", **item})
        self.toast("%s : %s" % (item["n"], text), item["c"])

    def post_system(self, text):
        item = {"u": 0, "n": "", "c": (0.8, 0.8, 0.8), "text": text, "t": time.time()}
        self.chat.append(item)
        self.broadcast({"type": "chat", **item})
        self.toast(text, item["c"])

    def post_mark(self, uid, loc):
        if not loc:
            return
        mark = {"u": uid, "loc": list(loc), "t": time.time()}
        self.pings.append(mark)
        self.broadcast({"type": "mark", "u": uid, "loc": list(loc)})
        compat.tag_redraw(("VIEW_3D",))

    def send_chat(self, text):
        self.post_chat(self.me, text)

    def send_mark(self, loc):
        self.post_mark(self.me, loc)

    def undo(self):
        self.request_undo(self.me)

    def redo(self):
        self.request_redo(self.me)

    def hist_undo(self, seq):
        self.undo_entry(self.me, seq)

    def hist_revert(self, seq):
        self.revert_to(self.me, seq)

    # ------------------------------------------------------------------
    # Gestion des joueurs
    # ------------------------------------------------------------------
    def kick(self, uid, reason="Exclu par l'hote."):
        for cid, c in list(self.clients.items()):
            if c.get("user") == uid:
                conn = self.server.get(cid)
                if conn:
                    conn.send({"type": "kick", "reason": reason})
                    conn.close_after_flush()

    def set_role(self, uid, role):
        user = self.users.get(uid)
        if user is None or uid == self.me:
            return
        user["role"] = role
        for cid, c in self.clients.items():
            if c.get("user") == uid:
                conn = self.server.get(cid)
                if conn:
                    conn.send({"type": "role", "role": role})
        self.broadcast(self._users_msg())
        self.post_system("%s est maintenant %s." % (user["name"],
                                                    "spectateur" if role == "viewer" else "editeur"))

    def set_sync_frame(self, value):
        self.sync_frame = bool(value)
        self.tracker.sync_frame = self.sync_frame
        self.broadcast({"type": "settings", "settings": {"sync_frame": self.sync_frame}})

    # ------------------------------------------------------------------
    # Sauvegardes
    # ------------------------------------------------------------------
    def _autosave_tick(self, now):
        p = _prefs()
        if p.autosave_minutes <= 0:
            return
        if now - self.autosave_t < p.autosave_minutes * 60:
            return
        if self.msg_seq == self.autosave_seq:
            self.autosave_t = now
            return
        if now - self.last_change_t < 1.5 and now - self.autosave_t < p.autosave_minutes * 60 + 120:
            return
        self.autosave()

    def _expire_tick(self, now):
        """Fait expirer les plus anciennes actions au-dela de la limite reglee,
        par lots, en archivant d'abord le projet."""
        if now - getattr(self, "_expire_t", 0.0) < 5.0 or self.history is None:
            return
        self._expire_t = now
        p = _prefs()
        limit = int(getattr(p, "history_limit", 500))
        if limit <= 0:
            return
        batch = max(10, limit // 10)
        if len(self.history.entries) < limit + batch:
            return
        if now - self.last_change_t < 1.5:
            return   # quelqu'un manipule : on attend un moment calme
        archive = ""
        if getattr(p, "archive_on_expire", True):
            archive = self.archive()
        n, upto = self.history.expire(limit)
        if n:
            for s in [s for s in self.hist if s <= upto]:
                self.hist.pop(s, None)
            self.broadcast({"type": "hist_expire", "upto": upto})
            self.log("%d actions anciennes expirees (jusqu'a #%d)%s" % (
                n, upto, (", archive " + os.path.basename(archive)) if archive else ""))
            compat.tag_redraw()

    def archive(self):
        folder = os.path.join(self.session_dir, "archives")
        path = os.path.join(folder, "archive_%05d_%s.blend" % (self.history.seq, _stamp()))
        try:
            self.tracker.smoother.finish_all()
            compat.save_copy(path)
            _rotate(folder, max(_prefs().max_backups, 5))
            self.history.flush()
            return path
        except Exception as exc:  # noqa: BLE001
            self.log("archive: %s" % exc)
            return ""

    def autosave(self):
        folder = os.path.join(self.session_dir, "autosave")
        path = os.path.join(folder, "autosave_%s.blend" % _stamp())
        try:
            self.tracker.smoother.finish_all()
            compat.save_copy(path)
            _rotate(folder, _prefs().max_backups)
            self.autosave_t = time.time()
            self.autosave_seq = self.msg_seq
            self.history.flush()
            self.log("autosauvegarde: %s" % path)
        except Exception as exc:  # noqa: BLE001
            self.log("autosauvegarde: %s" % exc)
        return path

    def checkpoint(self, label=""):
        self._flush_local()
        folder = os.path.join(self.session_dir, "checkpoints")
        n = len([f for f in os.listdir(folder)]) if os.path.isdir(folder) else 0
        path = os.path.join(folder, "%02d_%s.blend" % (n, _stamp()))
        compat.save_copy(path)
        entry, _ = self.history.add(self.me, self.username, self.color, [],
                                    "Point de sauvegarde" + (" : " + label if label else ""),
                                    kind="checkpoint", mergeable=False)
        entry["file"] = path
        self._hist_changed(entry)
        self.post_system("Point de sauvegarde cree par %s." % self.username)
        return path

    def backups(self):
        out = []
        for sub in ("checkpoints", "autosave", "archives", "backups"):
            folder = os.path.join(self.session_dir, sub)
            if not os.path.isdir(folder):
                continue
            for f in os.listdir(folder):
                if f.endswith(".blend"):
                    path = os.path.join(folder, f)
                    out.append((os.path.getmtime(path), sub, path))
        out.sort(reverse=True)
        return out

    def restore_file(self, path):
        """Recharge un point de sauvegarde et resynchronise tous les joueurs."""
        if not os.path.isfile(path):
            self.toast("Fichier introuvable.", (1, 0.4, 0.4))
            return False
        try:
            compat.save_copy(os.path.join(self.backups_dir(), "avant_restauration_%s.blend" % _stamp()))
        except Exception as exc:  # noqa: BLE001
            self.log("sauvegarde avant restauration: %s" % exc)
        self.history.flush()
        self.loading = True
        try:
            compat.open_file(path)
        finally:
            self.loading = False
        self.tracker = tracker_mod.Tracker(self)
        self.tracker.sync_frame = self.sync_frame
        self.tracker.init_known(write_blobs=True)
        entry, _ = self.history.add(self.me, self.username, self.color, [],
                                    "Restauration : %s" % os.path.basename(path), kind="restore",
                                    mergeable=False)
        self._hist_changed(entry)
        for cid in list(self.clients):
            if self.clients[cid].get("ready"):
                self._send_snapshot(cid)
        self.post_system("%s a restaure la sauvegarde %s." % (self.username, os.path.basename(path)))
        return True

    def stop(self, reason="arret par l'hote"):
        try:
            self.broadcast({"type": "kick", "reason": "L'hote a ferme la session."})
            if self.history is not None:
                self.history.flush()
            if self.active and self.msg_seq != self.autosave_seq:
                self.autosave()
        except Exception as exc:  # noqa: BLE001
            self.log("arret: %s" % exc)
        if self.tunnel is not None:
            self.tunnel.stop()
        if self.server is not None:
            time.sleep(0.1)
            self.server.stop()
        super().stop(reason)


# ===========================================================================
# Client
# ===========================================================================

class ClientSession(BaseSession):
    def __init__(self, username, color, viewer=False):
        super().__init__(username, color)
        self.conn = None
        self.address = ""
        self.port = 0
        self.password = ""
        self.viewer = viewer
        self.op_id = 0
        self.pending = {}
        self.incoming = {}         # textures envoyees apres la scene : sha -> reception
        self.stream_total = 0
        self.stream_got = 0
        self.local_files = {}
        self.snap_seq = -1
        self.rtt = 0
        self.ping_t = 0.0
        self.viewer_check_t = 0.0
        self.host_ver = ""
        self.snapshot_size = 0

    def connect(self, address, port, password="", timeout=8.0):
        global current
        self.address = address
        self.port = int(port)
        self.password = password or ""
        self.session_dir = os.path.join(root_dir(), "joined", "%s_%s" % (_safe(address), _stamp()))
        os.makedirs(self.session_dir, exist_ok=True)
        self.conn = net.connect(address, self.port, self.inbox, timeout=timeout)
        self.state = "auth"
        current = self
        self._start_timer()
        self.toast("Connexion a %s:%d..." % (address, self.port))
        return self

    def status_line(self):
        if self.state == "sync":
            pct = int(self.conn.rx_progress * 100) if self.conn else 0
            return "Multijoueur - Reception de la scene... %d%%" % pct
        if self.state == "ready":
            textures = ""
            if self.incoming and self.stream_total:
                textures = " - textures %d%%" % int(100 * self.stream_got / self.stream_total)
            return "Multijoueur - \"%s\" - %d joueur(s) - ping %d ms%s%s" % (
                self.session_name, len(self.users), self.rtt,
                " - spectateur" if self.role == "viewer" else "", textures)
        return "Multijoueur - connexion..."

    def send(self, msg, binary=b""):
        if self.conn is not None and self.conn.alive:
            self.conn.send(msg, binary)

    def send_presence(self, pres):
        self.send({"type": "presence", "p": pres})

    def send_live(self, msg, binary=b""):
        self.send(msg, binary)

    def live_backlog(self):
        return self.conn.queued if self.conn is not None else 0

    # ------------------------------------------------------------------
    def _tick(self, now):
        if self.reconcile_pending:
            self._reconcile()
        if self.active:
            if self.role == "viewer":
                if now - self.viewer_check_t > 0.5:
                    self.viewer_check_t = now
                    changes = self.tracker.collect(now)
                    if changes:
                        self.tracker.restore_known(uids=[c["u"] for c in changes])
                        self.toast("Mode spectateur : la scene ne peut pas etre modifiee.",
                                   (1, 0.6, 0.3))
            else:
                self.tracker.live.tick(now)
                self.tracker.poll_modes(now, set())
                changes = self.tracker.collect(now)
                if changes:
                    self._send_op(changes)
                    self.last_change_t = now
        self._process_inbox(now)
        if not self.active:
            return
        self.tracker.live.flush(now)
        self._presence_tick(now)
        self._enforce_locks()
        if now - self.ping_t > 2.0:
            self.ping_t = now
            self.send({"type": "ping", "t": now})
        self._local_backup_tick(now)

    def _send_op(self, changes):
        label = history.describe(changes, self.tracker.operator_label(), self.tracker.known)
        self.op_id += 1
        for c in changes:
            pend = self.pending.setdefault(c["u"], {})
            for k in c.get("f", {}):
                pend[k] = self.op_id
            pend["$"] = self.op_id
        self.tracker.mark_sent(changes)
        msg = {"type": "op", "id": self.op_id, "l": label, "ch": changes}
        binary = payloads.attach(msg, self.store)
        self.send(msg, binary)

    def flush(self):
        if self.active and self.role != "viewer":
            changes = self.tracker.collect(full=True)
            if changes:
                self._send_op(changes)

    def _filter_pending(self, changes):
        out = []
        for c in changes:
            pend = self.pending.get(c["u"])
            if pend and c["k"] == "u":
                drop = set(pend)
                if "fp" in drop or "blob" in drop or "geom" in drop:
                    drop |= {"fp", "blob", "file", "geom", "_mode"}
                f = {k: v for k, v in c["f"].items() if k not in drop}
                if not f:
                    continue
                c = dict(c, f=f)
            out.append(c)
        return out

    def _process_inbox(self, now):
        deadline = now + 0.06
        while time.time() < deadline:
            try:
                _cid, msg, binary = self.inbox.get_nowait()
            except queue.Empty:
                break
            if msg is None:
                if self.state != "closed":
                    reason = self.closed_reason or binary or "connexion perdue"
                    self.toast("Deconnecte : %s" % reason, (1, 0.4, 0.4))
                    self.stop(reason)
                return
            try:
                self._handle(msg, binary)
            except Exception:  # noqa: BLE001
                self.log("message %s: %s" % (msg.get("type"), traceback.format_exc()))
            if msg.get("type") == "snapshot":
                break  # laisse Blender respirer apres un chargement

    def _handle(self, msg, binary):
        t = msg.get("type")
        if binary and t not in ("snapshot", "filechunk"):
            payloads.detach(msg, binary, self.store)
        if t == "challenge":
            self.session_name = msg.get("name", "")
            if msg.get("proto") != net.PROTOCOL_VERSION:
                self.error = "Version du protocole differente de l'hote."
            self.send({"type": "hello", "proto": net.PROTOCOL_VERSION, "name": self.username,
                       "color": list(self.color), "ver": compat.VERSION_STR,
                       "addon": ADDON_VERSION, "viewer": self.viewer, "caps": ["manifest", "stream"],
                       "auth": _auth(self.password, msg.get("nonce", "")) if self.password else ""})
        elif t == "welcome":
            self.me = msg["user"]
            self.session_name = msg.get("session", "")
            self.sync_frame = bool(msg.get("settings", {}).get("sync_frame"))
            self.host_ver = msg.get("host_ver", "")
            # Un hote plus ancien ne relaie pas le flux : garder le chemin normal.
            self.tracker.live.enabled = bool(msg.get("live"))
            for s in msg.get("hist", []):
                self._update_hist(s)
            self.locks = msg.get("locks", {})
            for item in msg.get("chat", []):
                self.chat.append(item)
            self.role = "viewer" if self.viewer else "editor"
            self.state = "sync"
            self.toast("Connecte. Reception de la scene...")
        elif t == "manifest":
            self._on_manifest(msg)
        elif t == "filechunk":
            self._on_filechunk(msg, binary)
        elif t == "snapshot":
            self._on_snapshot(msg, binary)
        elif t == "live":
            if self.active:
                self.tracker.live.receive(msg, binary)
        elif t == "op":
            if msg.get("seq", 0) <= self.snap_seq or not self.active:
                return
            changes = self._filter_pending(msg.get("ch", []))
            if changes:
                errors = self.tracker.apply_remote(changes)
                self._after_errors(errors)
                compat.tag_redraw()
            self._update_hist(msg.get("h"))
        elif t == "ack":
            op_id = msg.get("id") or 0
            for uid in list(self.pending):
                pend = self.pending[uid]
                for k in [k for k, v in pend.items() if v <= op_id]:
                    del pend[k]
                if not pend:
                    del self.pending[uid]
            for uid, fix in (msg.get("fix") or {}).items():
                idb = self.tracker.resolve(uid)
                if idb is not None and "name" in fix and idb.name != fix["name"]:
                    idb.name = fix["name"]
                    self.tracker.accept(uid, self.tracker.attr_of.get(uid, ""), fix)
            rej = msg.get("rej") or {}
            if rej:
                self._apply_states(rej)
                self.toast("Modification refusee (objet en cours d'edition par un autre joueur).",
                           (1, 0.5, 0.3))
            self._update_hist(msg.get("h"))
        elif t == "state":
            self._apply_states(msg.get("s") or {})
        elif t == "hist":
            for s in msg.get("h", []):
                self._update_hist(s)
        elif t == "hist_expire":
            upto = msg.get("upto", 0)
            for s in [s for s in self.hist if s <= upto]:
                self.hist.pop(s, None)
        elif t == "presence":
            user = self.users.get(msg.get("u"))
            if user is not None:
                user["presence"] = msg.get("p", {})
                compat.tag_redraw(("VIEW_3D",))
        elif t == "users":
            old = self.users
            self.users = {}
            for u in msg.get("users", []):
                u["color"] = tuple(u.get("color", (1, 1, 1)))
                u["presence"] = old.get(u["id"], {}).get("presence", {})
                self.users[u["id"]] = u
            me = self.users.get(self.me)
            if me:
                self.role = me.get("role", self.role)
        elif t == "chat":
            item = {k: msg.get(k) for k in ("u", "n", "c", "text", "t")}
            item["c"] = tuple(item["c"] or (1, 1, 1))
            self.chat.append(item)
            self.toast(("%s : %s" % (item["n"], item["text"])) if item["n"] else item["text"],
                       item["c"])
        elif t == "mark":
            self.pings.append({"u": msg.get("u"), "loc": msg.get("loc"), "t": time.time()})
            compat.tag_redraw(("VIEW_3D",))
        elif t == "locks":
            self.locks = msg.get("l", {})
            compat.tag_redraw(("VIEW_3D",))
        elif t == "notice":
            self.toast(msg.get("text", ""), tuple(msg.get("c") or (1, 0.8, 0.3)))
        elif t == "pong":
            self.rtt = int((time.time() - (msg.get("t") or time.time())) * 1000)
        elif t == "role":
            self.role = msg.get("role", "editor")
            self.toast("Vous etes maintenant %s." % ("spectateur" if self.role == "viewer" else "editeur"))
        elif t == "settings":
            self.sync_frame = bool(msg.get("settings", {}).get("sync_frame"))
            self.tracker.sync_frame = self.sync_frame
        elif t == "kick":
            self.closed_reason = msg.get("reason", "")
            self.toast("Session terminee : %s" % self.closed_reason, (1, 0.4, 0.4))
            self.stop(self.closed_reason)

    def _after_errors(self, errors):
        fetch = [u for u, e in errors if e in ("payload", "missing")]
        if fetch:
            self.send({"type": "fetch", "uids": fetch})

    def _apply_states(self, states):
        changes = []
        for uid, st in states.items():
            self.pending.pop(uid, None)
            if st is None:
                if self.tracker.resolve(uid) is not None:
                    changes.append({"u": uid, "t": self.tracker.attr_of.get(uid, "objects"),
                                    "k": "d"})
                else:
                    self.tracker.known.pop(uid, None)
            else:
                exists = self.tracker.resolve(uid) is not None
                changes.append({"u": uid, "t": st["t"], "k": "u" if exists else "c",
                                "f": dict(st["f"])})
                if exists:
                    # on force la reecriture de tous les champs
                    self.tracker.known.setdefault(uid, {"t": st["t"], "f": {}})
        if changes:
            self.tracker.apply_remote(changes)
            for c in changes:
                if c["k"] == "u" and c["u"] in self.tracker.known:
                    self.tracker.accept(c["u"], c["t"], c["f"])
            compat.tag_redraw()

    def _on_snapshot(self, msg, binary):
        self.snapshot_size = len(binary)
        p = _prefs()
        if p.backup_before_join and (bpy.data.is_dirty or bpy.data.filepath):
            try:
                compat.save_copy(os.path.join(self.backups_dir(), "avant_synchro_%s.blend" % _stamp()))
            except Exception as exc:  # noqa: BLE001
                self.log("sauvegarde avant synchro: %s" % exc)
        path = os.path.join(self.session_dir, "%s.blend" % _safe(self.session_name or "session"))
        blend_len = msg.get("blend_len", len(binary))
        with open(path, "wb") as fh:
            fh.write(binary[:blend_len])
        # Les reglages de rendu restent ceux de ce PC (moteur, echantillons, GPU...).
        local_render = None
        try:
            if bpy.context.scene is not None:
                local_render = serialize.capture_local_render(bpy.context.scene)
        except Exception as exc:  # noqa: BLE001
            self.log("reglages de rendu locaux: %s" % exc)
        self.loading = True
        try:
            compat.open_file(path)
        finally:
            self.loading = False
        self.tracker = tracker_mod.Tracker(self)
        self.tracker.sync_frame = self.sync_frame
        px = self._apply_snapshot_extras(msg.get("ext") or [], binary, blend_len)
        if local_render:
            for sc in bpy.data.scenes:
                serialize.restore_local_render(sc, local_render, self.tracker)
        self.tracker.init_known(write_blobs=False)
        self.tracker.px_known.update(px)
        self.pending.clear()
        self.snap_seq = msg.get("seq", 0)
        self.active = True
        self.state = "ready"
        self.toast("Scene synchronisee (%d Ko). Bonne session !" % (len(binary) // 1024), (0.5, 1, 0.5))
        compat.tag_redraw()

    def _on_filechunk(self, msg, binary):
        sha = str(msg.get("sha", ""))
        inc = self.incoming.get(sha)
        if inc is None:
            return
        total = int(msg.get("total", -1))
        if total != inc["size"] or total > serialize.MAX_ATTACHED_FILE:
            self.incoming.pop(sha, None)
            return
        if inc["buf"] is None:
            inc["buf"] = bytearray(total)
        off = int(msg.get("off", 0))
        if off < 0 or off + len(binary) > total or off in inc["offs"]:
            return
        inc["offs"].add(off)
        inc["buf"][off:off + len(binary)] = binary
        inc["got"] += len(binary)
        self.stream_got += len(binary)
        if inc["got"] < total:
            return
        self.incoming.pop(sha, None)
        data = bytes(inc["buf"])
        if hashlib.sha256(data).hexdigest() != sha:
            self.log("texture %s corrompue, ignoree" % inc["fn"])
            return
        try:
            path = filecache.store(root_dir(), sha, inc["fn"], data)
        except OSError as exc:
            self.log("texture %s: %s" % (inc["fn"], exc))
            return
        for uid, attr, target in inc["targets"]:
            idb = self.tracker.resolve(uid)
            if idb is None:
                continue
            try:
                if os.path.normcase(bpy.path.abspath(idb.filepath)) != os.path.normcase(target):
                    continue   # remplacee entre-temps par une modification plus recente
                if idb.filepath != path:
                    idb.filepath = path   # meme contenu sous un autre nom : un seul fichier
                if hasattr(idb, "reload"):
                    idb.reload()
                # etat connu a jour : ce n'est pas une modification locale a diffuser
                self.tracker.accept(uid, attr or self.tracker.attr_of.get(uid, "images"),
                                    {"fp": 1, "fsig": 1})
            except Exception as exc:  # noqa: BLE001
                self.log("texture %s: %s" % (inc["fn"], exc))
        compat.tag_redraw()
        if not self.incoming:
            self.toast("Toutes les textures sont arrivees.", (0.5, 1, 0.5))

    def _on_manifest(self, msg):
        """Repond a l'hote avec les fichiers deja presents sur ce PC."""
        root = root_dir()
        self.local_files = {}
        have = []
        for f in (msg.get("files") or [])[:20000]:
            sha = str(f.get("sha", ""))
            if len(sha) != 64:
                continue
            path = filecache.find(root, sha, f.get("size"), f.get("fn"), str(f.get("path") or ""))
            if path:
                self.local_files[sha] = path
                have.append(sha)
        self.send({"type": "have", "sha": have})
        if have:
            self.log("%d fichier(s) deja presents sur ce PC, non retelecharges" % len(have))

    def _apply_snapshot_extras(self, ext, binary, offset):
        """Installe les fichiers externes et les pixels envoyes avec la scene."""
        folder = self.assets_dir()
        by_uid = {}
        for attr in serialize.FILE_ATTRS:
            for idb in getattr(bpy.data, attr, ()):
                u = ids.get_uid(idb)
                if u:
                    by_uid[u] = idb
        reload, px, received = [], {}, []
        self.incoming = {}
        for item in ext:
            size = int(item.get("len", 0))
            blob = binary[offset:offset + size]
            offset += size
            try:
                kind = item.get("k")
                if kind == "file":
                    idb = by_uid.get(item.get("u"))
                    if idb is None:
                        continue
                    if item.get("tile") is not None:
                        sub = os.path.join(folder, "udim_%s" % item["u"])
                        os.makedirs(sub, exist_ok=True)
                        with open(os.path.join(sub, item["fn"]), "wb") as fh:
                            fh.write(blob)
                        new = os.path.join(sub, item.get("pat") or item["fn"])
                    elif item.get("later"):
                        # Texture envoyee juste apres la scene : on la placera a son arrivee.
                        sha = str(item.get("sha", ""))
                        new = filecache.cached_path(root_dir(), sha, item["fn"])
                        inc = self.incoming.setdefault(sha, {
                            "fn": item["fn"], "size": int(item.get("size", 0)), "buf": None,
                            "got": 0, "offs": set(), "targets": []})
                        inc["targets"].append((item.get("u"), item.get("a"), new))
                        if idb.filepath != new:
                            idb.filepath = new
                        continue
                    elif item.get("have"):
                        new = getattr(self, "local_files", {}).get(item.get("sha"))
                        if not new:
                            self.log("fichier %s annonce present mais introuvable" % item.get("fn"))
                            continue
                    else:
                        sha = item.get("sha") or hashlib.sha256(blob).hexdigest()
                        new = filecache.store(root_dir(), sha, item["fn"], blob)
                        received.append(new)
                    if idb.filepath != new:
                        idb.filepath = new
                    if idb not in reload:
                        reload.append(idb)
                elif kind == "lib":
                    lib = bpy.data.libraries.get(item.get("n", ""))
                    if lib is None:
                        continue
                    sub = os.path.join(folder, "libs")
                    os.makedirs(sub, exist_ok=True)
                    new = os.path.join(sub, item["fn"])
                    with open(new, "wb") as fh:
                        fh.write(blob)
                    lib.filepath = new
                    lib.reload()
                elif kind == "px":
                    img = by_uid.get(item.get("u"))
                    if img is None:
                        continue
                    key = self.store.put(blob)
                    serialize.apply_pixels(img, {"w": item["w"], "h": item["h"], "c": item["c"],
                                                 "fmt": item.get("fmt"), "px": {"$p": key}}, self.tracker)
                    px[item["u"]] = key
            except Exception as exc:  # noqa: BLE001
                self.log("fichier joint %s: %s" % (item.get("fn") or item.get("u"), exc))
        for idb in reload:
            try:
                if hasattr(idb, "reload"):
                    idb.reload()
            except Exception:  # noqa: BLE001
                pass
        if ext:
            self.log("%d fichier(s)/texture(s) recu(s) avec la scene" % len(ext))
        self.stream_total = sum(i["size"] for i in self.incoming.values())
        self.stream_got = 0
        if self.incoming:
            self.log("%d texture(s) en cours de reception apres la scene" % len(self.incoming))
        try:
            filecache.trim(root_dir(), keep=received + list(getattr(self, "local_files", {}).values()))
        except OSError:
            pass
        return px

    # ------------------------------------------------------------------
    def send_chat(self, text):
        text = (text or "").strip()
        if text:
            self.send({"type": "chat", "text": text})

    def send_mark(self, loc):
        self.send({"type": "mark", "loc": list(loc)})

    def undo(self):
        self.flush()
        self.send({"type": "undo"})

    def redo(self):
        self.flush()
        self.send({"type": "redo"})

    def hist_undo(self, seq):
        self.flush()
        self.send({"type": "hist_undo", "seq": seq})

    def hist_revert(self, seq):
        self.flush()
        self.send({"type": "hist_revert", "seq": seq})

    def request_resync(self):
        self.send({"type": "resync"})

    def stop(self, reason="depart"):
        if self.conn is not None:
            try:
                if self.conn.alive:
                    self.conn.send({"type": "bye"})
                    self.conn.close_after_flush()
            except Exception:
                pass
        super().stop(reason)


# ===========================================================================
# API simple
# ===========================================================================

PORT_TRIES = 10
STREAM_MIN = 8 * 1024 * 1024       # au-dela, les textures manquantes partent apres la scene
STREAM_CHUNK = 512 * 1024


def port_in_use(exc):
    """Port deja pris (10048) ou reserve par Windows (10013)."""
    return getattr(exc, "winerror", None) in (10048, 10013) or         getattr(exc, "errno", None) in (errno.EADDRINUSE, errno.EACCES)


def open_server(port, inbox):
    """Ouvre le serveur sur `port`, ou sur l'un des ports suivants s'il est
    deja pris (autre Blender qui heberge sur ce PC...). Renvoie (serveur, port)."""
    first = None
    for p in range(port, min(port + PORT_TRIES, 65536)):
        try:
            return net.Server(p, inbox), p
        except OSError as exc:
            if not port_in_use(exc):
                raise
            first = first or exc
    raise OSError(first.errno, "les ports %d a %d sont deja utilises sur ce PC (%s)"
                  % (port, port + PORT_TRIES - 1, first.strerror or first))


_ORPHAN_SESSIONS = ("HostSession", "ClientSession")
_ORPHAN_TRANSPORTS = ("Server", "Tunnel")


def stop_orphans():
    """Arrete les sessions, serveurs et tunnels encore actifs qui ne sont pas
    la session courante : restes d'une ancienne version de l'addon apres une
    mise a jour ou un rechargement des scripts. Sans cela ils gardent le port
    ouvert ("port deja utilise") et continuent d'echanger en arriere-plan.
    Renvoie le nombre d'objets arretes."""
    cur = current
    keep = set()
    if cur is not None:
        keep = {id(cur), id(getattr(cur, "server", None)), id(getattr(cur, "tunnel", None))}
    found = [o for o in gc.get_objects()
             if type(o).__name__ in _ORPHAN_SESSIONS + _ORPHAN_TRANSPORTS
             and "blender_multiplayer" in (type(o).__module__ or "") and id(o) not in keep]
    n = 0
    for o in found:
        if type(o).__name__ in _ORPHAN_SESSIONS and getattr(o, "state", "closed") != "closed":
            try:
                o.stop("session orpheline")
            except Exception:  # noqa: BLE001
                pass
            n += 1
    for o in found:
        if type(o).__name__ in _ORPHAN_TRANSPORTS and getattr(o, "running", False):
            try:
                o.stop()
            except Exception:  # noqa: BLE001
                pass
            n += 1
    if n:
        print("[Multiplayer] %d element(s) d'une session precedente arrete(s)" % n)
    return n


def host(port, password="", session_name="Session", username=None, color=None, sync_frame=False,
         online=False, relay=""):
    global current, last_reason
    last_reason = ""
    if current is not None:
        current.stop("nouvelle session")
    stop_orphans()
    p = _prefs()
    s = HostSession(username or p.username or "Hote", color or tuple(p.color))
    try:
        s.start(port, password, session_name, sync_frame=sync_frame, online=online, relay=relay)
    except Exception:
        current = None
        if s.tunnel is not None:
            s.tunnel.stop()
        if s.server is not None:
            s.server.stop()
        raise
    return s


def join(address, port, password="", username=None, color=None, viewer=False):
    global current, last_reason
    last_reason = ""
    if current is not None:
        current.stop("nouvelle session")
    stop_orphans()
    p = _prefs()
    s = ClientSession(username or p.username or "Joueur", color or tuple(p.color), viewer=viewer)
    s.connect(address, port, password, timeout=invite.connect_timeout(address))
    return s


def join_any(addresses, password="", username=None, color=None, viewer=False):
    """Essaie chaque adresse (hote, port) dans l'ordre ; renvoie la session
    connectee ou leve OSError avec le detail de chaque essai."""
    errors = []
    for address, port in addresses:
        try:
            return join(address, port, password, username=username, color=color, viewer=viewer)
        except OSError as exc:
            errors.append("%s:%d (%s)" % (address, port, describe_error(exc, address)))
    raise OSError("aucune adresse ne repond : " + " ; ".join(errors))


def describe_error(exc, address=""):
    """Message clair pour un echec de connexion."""
    if isinstance(exc, ConnectionRefusedError) or getattr(exc, "winerror", None) == 10061:
        msg = "personne n'heberge de session a cette adresse et ce port"
        if address in ("127.0.0.1", "localhost", "::1"):
            msg += " (127.0.0.1 designe votre propre PC : utilisez le lien ou l'adresse de l'hote)"
        return msg
    if isinstance(exc, (TimeoutError, socket_timeout())):
        return "l'hote ne repond pas (pas sur le meme reseau ? utilisez le lien d'invitation)"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (11001, -2, -3, 8):
        return "adresse introuvable"
    return str(exc)


def socket_timeout():
    import socket
    return socket.timeout


def leave():
    global current
    if current is not None:
        current.stop("depart")
    current = None
