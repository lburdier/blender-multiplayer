"""Transport reseau : TCP, trames binaires, threads d'envoi et de reception.

Aucun appel a bpy ici : ce module tourne dans des threads. Les messages
recus sont deposes dans une file (inbox) lue par le thread principal de
Blender (timer de la session).

Trame : en-tete fixe + corps (JSON utf-8 suivi d'une piece jointe binaire),
le corps etant compresse en zlib quand c'est rentable.
"""

import json
import queue
import socket
import struct
import threading
import time
import zlib

PROTOCOL_VERSION = 4
MAGIC = b"BMP3"
_HDR = struct.Struct("!4sBIQQ")  # magic, flags, json_len, bin_len, body_len
FLAG_ZLIB = 1
MAX_BODY = 4 * 1024 * 1024 * 1024
_CLOSE = object()


class ProtocolError(Exception):
    pass


def encode(msg, binary=b""):
    js = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = js + binary if binary else js
    flags = 0
    if len(body) > 4096:
        z = zlib.compress(body, 1)
        if len(z) < len(body) * 0.9:
            body = z
            flags |= FLAG_ZLIB
    return _HDR.pack(MAGIC, flags, len(js), len(binary), len(body)), body


def _recv_exact(sock, n, progress=None):
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], min(n - got, 1 << 20))
        if r == 0:
            raise ConnectionError("connexion fermee par le pair")
        got += r
        if progress is not None:
            progress(got, n)
    return bytes(buf)


class Connection:
    """Une connexion TCP avec un thread lecteur et un thread ecrivain."""

    def __init__(self, sock, cid, inbox):
        self.sock = sock
        self.cid = cid
        self.inbox = inbox
        self.out = queue.Queue()
        self.alive = True
        self._lock = threading.Lock()
        self._dead_reported = False
        self.last_rx = time.time()
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.rx_expected = 0
        self.rx_got = 0
        try:
            self.peer = sock.getpeername()[0]
        except OSError:
            self.peer = "?"
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        self._threads = []

    def start(self):
        for target in (self._reader, self._writer):
            t = threading.Thread(target=target, daemon=True,
                                 name="mp-%s-%s" % (target.__name__, self.cid))
            t.start()
            self._threads.append(t)

    # -- API --------------------------------------------------------------
    def send(self, msg, binary=b""):
        if self.alive:
            self.out.put((msg, binary))

    def close_after_flush(self):
        if self.alive:
            self.out.put(_CLOSE)

    def close(self, reason="fermeture locale"):
        self._die(reason)

    @property
    def queued(self):
        return self.out.qsize()

    @property
    def rx_progress(self):
        if self.rx_expected <= 0:
            return 1.0
        return min(1.0, self.rx_got / float(self.rx_expected))

    # -- Threads ------------------------------------------------------------
    def _progress(self, got, total):
        self.rx_got = got
        self.last_rx = time.time()

    def _writer(self):
        reason = "erreur d'envoi"
        try:
            while self.alive:
                item = self.out.get()
                if item is None:
                    break
                if item is _CLOSE:
                    reason = "fermeture"
                    break
                hdr, body = encode(*item)
                self.sock.sendall(hdr)
                self.sock.sendall(body)
                self.tx_bytes += len(hdr) + len(body)
        except Exception as exc:  # noqa: BLE001
            reason = "envoi: %s" % exc
        finally:
            self._die(reason)

    def _reader(self):
        reason = "connexion perdue"
        try:
            while self.alive:
                hdr = _recv_exact(self.sock, _HDR.size)
                magic, flags, jl, bl, body_len = _HDR.unpack(hdr)
                if magic != MAGIC:
                    raise ProtocolError("protocole incompatible")
                if body_len > MAX_BODY:
                    raise ProtocolError("trame trop grande")
                self.rx_expected = body_len
                self.rx_got = 0
                body = _recv_exact(self.sock, body_len, self._progress)
                self.rx_expected = 0
                if flags & FLAG_ZLIB:
                    body = zlib.decompress(body)
                msg = json.loads(body[:jl].decode("utf-8"))
                binary = body[jl:jl + bl] if bl else b""
                self.last_rx = time.time()
                self.rx_bytes += len(hdr) + body_len
                self.inbox.put((self.cid, msg, binary))
        except Exception as exc:  # noqa: BLE001
            reason = str(exc) or reason
        finally:
            self._die(reason)

    def _die(self, reason):
        with self._lock:
            if self._dead_reported:
                return
            self._dead_reported = True
            self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.out.put(None)
        self.inbox.put((self.cid, None, reason))


class Server:
    """Serveur TCP : accepte les joueurs et cree une Connection par joueur."""

    def __init__(self, port, inbox, host=""):
        self.inbox = inbox
        self.port = port
        self.conns = {}
        self._next = 1
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        else:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((host, port))
            self.sock.listen(16)
        except OSError:
            self.sock.close()
            raise
        self._thread = threading.Thread(target=self._accept, daemon=True, name="mp-accept")
        self._thread.start()

    def _accept(self):
        while self.running:
            try:
                s, _addr = self.sock.accept()
            except OSError:
                break
            if not self.running:
                s.close()
                break
            cid = self._next
            self._next += 1
            conn = Connection(s, cid, self.inbox)
            self.conns[cid] = conn
            self.inbox.put((cid, {"type": "__connect__", "addr": conn.peer}, b""))
            conn.start()

    def get(self, cid):
        return self.conns.get(cid)

    def drop(self, cid):
        conn = self.conns.pop(cid, None)
        if conn is not None:
            conn.close()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        for conn in list(self.conns.values()):
            conn.close("arret du serveur")
        self.conns.clear()


def connect(address, port, inbox, timeout=8.0):
    s = socket.create_connection((address, int(port)), timeout=timeout)
    s.settimeout(None)
    conn = Connection(s, 0, inbox)
    conn.start()
    return conn


def local_addresses():
    """Adresses IP locales utiles a communiquer aux autres joueurs."""
    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))  # aucun paquet n'est envoye
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    if not found:
        found.append("127.0.0.1")
    return found
