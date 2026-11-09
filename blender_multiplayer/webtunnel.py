"""Deuxieme voie Internet, par le port 443 (HTTPS) : passe la ou bore.pub est bloque.

Beaucoup de reseaux (ecoles, entreprises, Wi-Fi publics) ne laissent sortir que
le web (ports 80 et 443). Le relais bore.pub attribue un port au hasard
(ex. 42304) : depuis ces reseaux l'invite ne le joint jamais ("l'hote ne repond
pas"). Cette voie passe par un tunnel rapide Cloudflare (trycloudflare.com,
gratuit, sans compte) :

    invite --wss:443--> Cloudflare --> cloudflared (chez l'hote)
           --> pont WebSocket local --> serveur de la session (127.0.0.1:port)

L'hote telecharge une fois le programme officiel cloudflared (GitHub de
Cloudflare) dans le dossier de l'addon. L'invite n'a besoin de rien : une
connexion HTTPS ordinaire. Le protocole de la session reste authentifie par mot
de passe (HMAC) et le trajet est chiffre (TLS).

Aucun appel a bpy ici.
"""

import base64
import hashlib
import os
import platform
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.request

SCHEME = "wss://"
PATH = "/blender-multiplayer"
_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_URL_RE = re.compile(r"https://([a-z0-9-]+\.trycloudflare\.com)")
_RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
_CHUNK = 64 * 1024
_MAX_HEADER = 16 * 1024


def is_web_address(host):
    return str(host).lower().startswith(SCHEME)


def host_of(address):
    """'wss://abc.trycloudflare.com/...' -> 'abc.trycloudflare.com'."""
    text = str(address)
    if is_web_address(text):
        text = text[len(SCHEME):]
    return text.split("/", 1)[0].split(":", 1)[0].strip().lower()


# ----------------------------------------------------------------------
# Trames WebSocket (RFC 6455), le strict necessaire : donnees binaires,
# ping / pong, fermeture.

def _frame(opcode, payload, mask):
    n = len(payload)
    head = bytes([0x80 | opcode])
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack("!H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack("!Q", n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + _xor(payload, key)


def _xor(data, key):
    if not data:
        return b""
    k = (key * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(k, "little")).to_bytes(len(data), "little")


class _FrameReader:
    """Decoupe un flux d'octets en trames ; renvoie [(opcode, donnees)]."""

    def __init__(self):
        self.buf = bytearray()
        self.parts = []          # trame fragmentee en cours

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            b = self.buf
            if len(b) < 2:
                break
            fin, opcode = b[0] & 0x80, b[0] & 0x0F
            masked, n = b[1] & 0x80, b[1] & 0x7F
            pos = 2
            if n == 126:
                if len(b) < 4:
                    break
                n = struct.unpack("!H", bytes(b[2:4]))[0]
                pos = 4
            elif n == 127:
                if len(b) < 10:
                    break
                n = struct.unpack("!Q", bytes(b[2:10]))[0]
                pos = 10
            key = b""
            if masked:
                if len(b) < pos + 4:
                    break
                key = bytes(b[pos:pos + 4])
                pos += 4
            if len(b) < pos + n:
                break
            payload = bytes(b[pos:pos + n])
            del b[:pos + n]
            if masked:
                payload = _xor(payload, key)
            if opcode >= 8:                  # controle : jamais fragmente
                out.append((opcode, payload))
                continue
            self.parts.append(payload)
            if fin:
                out.append((2, b"".join(self.parts)))
                self.parts = []
        return out


def _accept_key(key):
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + _GUID).digest()).decode("ascii")


def _read_header(sock, limit=_MAX_HEADER):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connexion fermee pendant l'ouverture")
        data += chunk
        if len(data) > limit:
            raise ConnectionError("en-tete trop long")
    head, _, rest = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if sep:
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers, rest


# ----------------------------------------------------------------------
# Cote invite

def _ssl_context():
    ctx = ssl.create_default_context()
    try:
        import certifi  # fourni avec Blender sur la plupart des plateformes
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


_DOH = ("https://cloudflare-dns.com/dns-query", "https://1.1.1.1/dns-query", "https://dns.google/resolve")


def resolve_doh(host, timeout=5.0):
    """Resolution par DNS-over-HTTPS (port 443). Une adresse trycloudflare toute
    neuve n'est souvent pas encore connue du DNS de la box, qui garde ensuite cet
    echec en cache plusieurs minutes : on demande directement a un resolveur public."""
    import json
    from urllib.parse import quote
    for base in _DOH:
        try:
            req = urllib.request.Request("%s?name=%s&type=A" % (base, quote(host)),
                                         headers={"accept": "application/dns-json",
                                                  "User-Agent": "blender-multiplayer"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for ans in data.get("Answer") or ():
                if ans.get("type") == 1 and ans.get("data"):
                    return ans["data"]
        except Exception:  # noqa: BLE001
            continue
    return ""


def resolve(host):
    try:
        return socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        pass
    ip = resolve_doh(host)
    if not ip:
        raise socket.gaierror(11001, "adresse introuvable : %s" % host)
    return ip


def connect(address, timeout=12.0):
    """Ouvre la voie HTTPS vers l'hote et renvoie une socket ordinaire (une
    extremite de socketpair) : le reste de l'addon l'utilise comme une
    connexion TCP directe."""
    host = host_of(address)
    ip = resolve(host)
    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        tls = _ssl_context().wrap_socket(raw, server_hostname=host)
    except ssl.SSLCertVerificationError:
        # Certificats systeme absents (certains Linux / macOS) : le trajet reste
        # chiffre, et le mot de passe de la session est verifie de toute facon.
        raw.close()
        raw = socket.create_connection((ip, 443), timeout=timeout)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(raw, server_hostname=host)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        tls.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n"
                     "User-Agent: blender-multiplayer\r\n\r\n" % (PATH, host, key)).encode("ascii"))
        status, headers, rest = _read_header(tls)
        if " 101 " not in status + " ":
            code = status.split(" ")[1] if len(status.split(" ")) > 1 else status
            if code in ("502", "503", "530", "404"):
                raise ConnectionRefusedError(
                    "l'hote n'heberge plus a cette adresse (session fermee ou lien perime)")
            raise ConnectionError("reponse inattendue du tunnel : %s" % status[:80])
        if headers.get("sec-websocket-accept") != _accept_key(key):
            raise ConnectionError("reponse WebSocket invalide")
    except Exception:
        tls.close()
        raise
    tls.settimeout(None)
    mine, theirs = socket.socketpair()
    threading.Thread(target=_client_pump, args=(tls, theirs, rest), daemon=True,
                     name="mp-web-client").start()
    return mine


def _client_pump(tls, local, early):
    """Un seul thread touche a la socket TLS (lecture et ecriture) : select."""
    import select
    reader = _FrameReader()
    last_ping = time.time()
    try:
        for op, data in reader.feed(early):
            if op == 2 and data:
                local.sendall(data)
        while True:
            ready = [tls] if tls.pending() else select.select([tls, local], [], [], 15.0)[0]
            if tls in ready:
                data = tls.recv(_CHUNK)
                if not data:
                    break
                for op, payload in reader.feed(data):
                    if op == 2:
                        if payload:
                            local.sendall(payload)
                    elif op == 8:
                        raise ConnectionError("fermee")
                    elif op == 9:
                        tls.sendall(_frame(10, payload, True))
            if local in ready:
                data = local.recv(_CHUNK)
                if not data:
                    break
                tls.sendall(_frame(2, data, True))
            if time.time() - last_ping > 20.0:   # garde la connexion ouverte chez Cloudflare
                last_ping = time.time()
                tls.sendall(_frame(9, b"mp", True))
    except (OSError, ValueError, ConnectionError):
        pass
    for s in (tls, local):
        try:
            s.close()
        except OSError:
            pass


# ----------------------------------------------------------------------
# Cote hote : pont WebSocket local -> serveur de la session

class _Bridge:
    def __init__(self, local_port, track, untrack):
        self.local_port = int(local_port)
        self.track, self.untrack = track, untrack
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._accept, daemon=True, name="mp-web-accept").start()

    def _accept(self):
        while self.running:
            try:
                s, _addr = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(s,), daemon=True,
                             name="mp-web-serve").start()

    def _serve(self, ws):
        self.track(ws)
        local = None
        try:
            ws.settimeout(15)
            line, headers, rest = _read_header(ws)
            if headers.get("upgrade", "").lower() != "websocket" or "sec-websocket-key" not in headers:
                body = b"Blender Multiplayer : ouvrez Blender et utilisez le lien d'invitation.\n"
                ws.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
                raise ConnectionError("pas une demande WebSocket")
            local = socket.create_connection(("127.0.0.1", self.local_port), timeout=5)
            self.track(local)
            ws.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                        "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n"
                        % _accept_key(headers["sec-websocket-key"])).encode("ascii"))
            for s in (ws, local):
                s.settimeout(None)
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            lock = threading.Lock()
            threading.Thread(target=self._to_ws, args=(local, ws, lock), daemon=True,
                             name="mp-web-out").start()
            reader = _FrameReader()
            data = rest
            while self.running:
                for op, payload in reader.feed(data):
                    if op == 2:
                        if payload:
                            local.sendall(payload)
                    elif op == 8:
                        raise ConnectionError("fermee")
                    elif op == 9:
                        with lock:
                            ws.sendall(_frame(10, payload, False))
                data = ws.recv(_CHUNK)
                if not data:
                    break
        except (OSError, ValueError, ConnectionError):
            pass
        for s in (ws, local):
            if s is not None:
                self.untrack(s)

    def _to_ws(self, local, ws, lock):
        try:
            while self.running:
                data = local.recv(_CHUNK)
                if not data:
                    break
                with lock:
                    ws.sendall(_frame(2, data, False))
        except OSError:
            pass
        for s in (ws, local):
            self.untrack(s)

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


# ----------------------------------------------------------------------
# Cote hote : programme cloudflared

def asset_name():
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64") or machine.startswith("armv8")
    if sys.platform == "win32":
        return "cloudflared-windows-amd64.exe"      # tourne aussi sur Windows ARM
    if sys.platform == "darwin":
        return "cloudflared-darwin-%s.tgz" % ("arm64" if arm else "amd64")
    if machine.startswith("arm") and not arm:
        return "cloudflared-linux-arm"
    return "cloudflared-linux-%s" % ("arm64" if arm else "amd64")


def exe_path(bin_dir):
    return os.path.join(bin_dir, "cloudflared.exe" if sys.platform == "win32" else "cloudflared")


def ensure_cloudflared(bin_dir, progress=None):
    """Telecharge cloudflared une seule fois ; renvoie son chemin."""
    path = exe_path(bin_dir)
    if os.path.isfile(path) and os.path.getsize(path) > 1000000:
        return path
    os.makedirs(bin_dir, exist_ok=True)
    name = asset_name()
    tmp = path + ".part"
    req = urllib.request.Request(_RELEASES + name, headers={"User-Agent": "blender-multiplayer"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if progress is not None and total:
                progress(got / total)
    if name.endswith(".tgz"):
        import tarfile
        with tarfile.open(tmp, "r:gz") as tar:
            member = next(m for m in tar.getmembers() if m.isfile() and
                          os.path.basename(m.name) == "cloudflared")
            with tar.extractfile(member) as src, open(path + ".x", "wb") as dst:
                dst.write(src.read())
        os.remove(tmp)
        tmp = path + ".x"
    if sys.platform != "win32":
        os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


class _KillOnClose:
    """Windows : rattache cloudflared a un "job" ferme avec Blender, pour qu'il
    ne survive jamais a Blender (meme apres un plantage)."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE

            class _Basic(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("c", ctypes.c_size_t),
                            ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                            ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

            class _Ext(ctypes.Structure):
                _fields_ = [("basic", _Basic), ("io", ctypes.c_uint64 * 6),
                            ("p", ctypes.c_size_t), ("j", ctypes.c_size_t),
                            ("pp", ctypes.c_size_t), ("pj", ctypes.c_size_t)]

            job = k32.CreateJobObjectW(None, None)
            info = _Ext()
            info.basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.handle, self.k32 = job, k32
        except Exception:  # noqa: BLE001
            self.handle = None

    def attach(self, pid):
        if not self.handle:
            return
        try:
            h = self.k32.OpenProcess(0x1F0FFF, False, pid)
            if h:
                self.k32.AssignProcessToJobObject(self.handle, h)
                self.k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass


_JOB = None
_PROCS = set()


def _kill_all():
    for p in list(_PROCS):
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


import atexit  # noqa: E402
atexit.register(_kill_all)


class WebTunnel:
    """Meme interface que tunnel.Tunnel : `state`, `public_address`, `error`,
    `generation`, `running`, `stop()`. Tourne dans ses propres threads."""

    def __init__(self, local_port, bin_dir):
        self.local_port = int(local_port)
        self.bin_dir = bin_dir
        self.state = "connecting"      # download / connecting / open / retry / closed
        self.error = ""
        self.progress = 0.0
        self.generation = 0
        self.running = True
        self.host = ""
        self.proc = None
        self._socks = set()
        self._lock = threading.Lock()
        self.bridge = _Bridge(self.local_port, self._track, self._untrack)
        self._thread = threading.Thread(target=self._run, daemon=True, name="mp-web-tunnel")
        self._thread.start()

    @property
    def public_address(self):
        if self.state != "open" or not self.host:
            return ""
        return SCHEME + self.host

    def _track(self, sock):
        with self._lock:
            self._socks.add(sock)

    def _untrack(self, sock):
        with self._lock:
            self._socks.discard(sock)
        try:
            sock.close()
        except OSError:
            pass

    def _progress(self, frac):
        self.progress = frac

    def _run(self):
        global _JOB
        delay = 3.0
        while self.running:
            try:
                if not os.path.isfile(exe_path(self.bin_dir)):
                    self.state = "download"
                exe = ensure_cloudflared(self.bin_dir, self._progress)
                if not self.running:
                    break
                self.state = "connecting"
                if _JOB is None:
                    _JOB = _KillOnClose()
                self._session(exe)
                delay = 3.0
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
            delay = min(delay * 2, 60.0)
        self.state = "closed"

    def _session(self, exe):
        flags = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % self.bridge.port],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=flags)
        self.proc = proc
        _PROCS.add(proc)
        _JOB.attach(proc.pid)
        host, tail = "", []
        try:
            for raw in proc.stdout:
                if not self.running:
                    break
                line = raw.decode("utf-8", "replace").strip()
                tail = (tail + [line])[-6:]
                m = _URL_RE.search(line)
                if m and not host and "api.trycloudflare.com" not in m.group(1):
                    host = m.group(1)
                if host and "Registered tunnel connection" in line and self.state != "open":
                    self._wait_dns(host)
                    if not self.running:
                        break
                    self.host = host
                    self.error = ""
                    self.generation += 1
                    self.state = "open"
        finally:
            _PROCS.discard(proc)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
        if self.running:
            msg = next((x for x in reversed(tail) if " ERR " in x or "error" in x.lower()), "")
            raise ConnectionError("cloudflared arrete%s" % ((" : " + msg[-120:]) if msg else ""))

    def _wait_dns(self, host):
        """N'annonce l'adresse qu'une fois connue des resolveurs publics."""
        end = time.time() + 30
        while self.running and time.time() < end:
            if resolve_doh(host, timeout=3):
                return
            time.sleep(1.0)

    def stop(self):
        self.running = False
        p = self.proc
        if p is not None:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        self.bridge.stop()
        with self._lock:
            socks = list(self._socks)
        for s in socks:
            self._untrack(s)
        self.state = "closed"
