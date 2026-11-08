"""Tunnel Internet : rend la session joignable hors du reseau local, sans VPN
ni redirection de port sur la box.

L'hote ouvre une connexion sortante vers un relais TCP public compatible
"bore" (https://github.com/ekzhang/bore, serveur public gratuit bore.pub).
Le relais attribue un port public ; chaque joueur qui s'y connecte est relaye
vers le serveur local de l'hote. Le protocole de la session reste authentifie
par mot de passe (HMAC) ; le relais ne voit passer que des trames compressees.

Protocole bore : messages JSON termines par un octet nul.
  client -> relais : {"Hello": port}   {"Accept": uuid}
  relais -> client : {"Hello": port}   "Heartbeat"   {"Connection": uuid}   {"Error": "..."}
"""

import json
import socket
import threading
import time

DEFAULT_RELAY = "bore.pub:7835"
_MAX_FRAME = 64 * 1024


def parse_relay(text):
    text = (text or "").strip() or DEFAULT_RELAY
    host, sep, port = text.rpartition(":")
    if not sep:
        return text, 7835
    try:
        return host, int(port)
    except ValueError:
        return text, 7835


class _Reader:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def read(self):
        while b"\0" not in self.buf:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("le relais a ferme la connexion")
            self.buf += data
            if len(self.buf) > _MAX_FRAME:
                raise ConnectionError("reponse du relais invalide")
        raw, _, self.buf = self.buf.partition(b"\0")
        return json.loads(raw.decode("utf-8"))


def _send(sock, obj):
    sock.sendall(json.dumps(obj).encode("utf-8") + b"\0")


def _nodelay(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


class Tunnel:
    """Tunnel vers un relais bore. Tourne dans ses propres threads ;
    lire `state`, `public_address` et `error` depuis le thread principal."""

    def __init__(self, local_port, relay=DEFAULT_RELAY):
        self.local_port = int(local_port)
        self.relay_host, self.relay_port = parse_relay(relay)
        self.remote_port = 0
        self.state = "connecting"      # connecting / open / retry / closed
        self.error = ""
        self.generation = 0            # incremente a chaque (re)ouverture
        self.running = True
        self._socks = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mp-tunnel")
        self._thread.start()

    # ------------------------------------------------------------------
    @property
    def public_address(self):
        if self.state != "open" or not self.remote_port:
            return ""
        return "%s:%d" % (self.relay_host, self.remote_port)

    def _track(self, sock):
        with self._lock:
            self._socks.add(sock)

    def _close(self, *socks):
        for sock in socks:
            with self._lock:
                self._socks.discard(sock)
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    def _run(self):
        delay = 2.0
        while self.running:
            try:
                self._session()
                delay = 2.0
            except Exception as exc:  # noqa: BLE001
                if not self.running:
                    break
                self.error = str(exc) or exc.__class__.__name__
            if not self.running:
                break
            self.state = "retry"
            end = time.time() + delay
            while self.running and time.time() < end:
                time.sleep(0.2)
            delay = min(delay * 2, 30.0)
        self.state = "closed"

    def _session(self):
        ctrl = socket.create_connection((self.relay_host, self.relay_port), timeout=10)
        self._track(ctrl)
        try:
            _nodelay(ctrl)
            reader = _Reader(ctrl)
            # Redemande le meme port apres une coupure : le lien partage reste valable.
            _send(ctrl, {"Hello": self.remote_port})
            msg = reader.read()
            if isinstance(msg, dict) and "Challenge" in msg:
                raise ConnectionError("ce relais exige un secret (non pris en charge)")
            if not (isinstance(msg, dict) and "Hello" in msg):
                err = msg.get("Error") if isinstance(msg, dict) else str(msg)
                if self.remote_port:
                    self.remote_port = 0   # port repris par quelqu'un d'autre : en demander un neuf
                raise ConnectionError("relais : %s" % err)
            self.remote_port = int(msg["Hello"])
            self.error = ""
            self.generation += 1
            self.state = "open"
            ctrl.settimeout(20)            # le relais envoie un Heartbeat toutes les 500 ms
            while self.running:
                msg = reader.read()
                if msg == "Heartbeat":
                    continue
                if isinstance(msg, dict) and "Connection" in msg:
                    threading.Thread(target=self._proxy, args=(msg["Connection"],), daemon=True,
                                     name="mp-tunnel-proxy").start()
                elif isinstance(msg, dict) and "Error" in msg:
                    raise ConnectionError("relais : %s" % msg["Error"])
        finally:
            self._close(ctrl)

    def _proxy(self, conn_id):
        try:
            remote = socket.create_connection((self.relay_host, self.relay_port), timeout=10)
        except OSError:
            return
        self._track(remote)
        try:
            _send(remote, {"Accept": conn_id})
            local = socket.create_connection(("127.0.0.1", self.local_port), timeout=5)
        except OSError:
            self._close(remote)
            return
        self._track(local)
        for sock in (remote, local):
            sock.settimeout(None)
            _nodelay(sock)
        threading.Thread(target=self._pipe, args=(remote, local), daemon=True,
                         name="mp-tunnel-pipe").start()
        self._pipe(local, remote)

    def _pipe(self, src, dst):
        try:
            while self.running:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        self._close(src, dst)

    def stop(self):
        self.running = False
        with self._lock:
            socks = list(self._socks)
        self._close(*socks)
        self.state = "closed"
