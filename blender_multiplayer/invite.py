"""Liens d'invitation prives.

L'hote copie un lien du type
    https://lburdier.github.io/blender-multiplayer/join#<jeton>
et l'envoie (Discord, mail...). Le jeton contient le nom de la session, les
adresses ou joindre l'hote (Internet + reseau local) et le mot de passe. Il est
place apres le '#', partie de l'URL qui n'est jamais envoyee au serveur web :
ouvrir le lien dans un navigateur affiche seulement le mode d'emploi.

Dans Blender, "Rejoindre avec le lien copie" lit le presse-papiers et se
connecte en essayant les adresses dans l'ordre.
"""

import base64
import ipaddress
import json
import re

JOIN_PAGE = "https://lburdier.github.io/blender-multiplayer/join"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{8,}")


def make(session_name, addresses, password):
    """addresses : liste de (hote, port) par ordre de preference."""
    data = {"v": 1, "n": session_name, "a": [[h, int(p)] for h, p in addresses], "k": password or ""}
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return "%s#%s" % (JOIN_PAGE, token)


def parse(text):
    """Renvoie {"name", "addresses": [(hote, port)], "password"} ou None."""
    if not text:
        return None
    text = text.strip()
    if len(text) > 4096:
        return None
    token = text.split("#", 1)[1] if "#" in text else text
    token = token.strip().strip("/")
    m = _TOKEN_RE.fullmatch(token)
    if not m:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        data = json.loads(raw.decode("utf-8"))
        addrs = []
        for item in data["a"]:
            host, port = str(item[0]).strip(), int(item[1])
            if host and 0 < port < 65536:
                addrs.append((host, port))
        if not addrs:
            return None
        return {"name": str(data.get("n", ""))[:64], "addresses": addrs,
                "password": str(data.get("k", ""))}
    except Exception:  # noqa: BLE001
        return None


def parse_address(text, default_port):
    """'1.2.3.4', '1.2.3.4:5555', 'bore.pub:41234', '[::1]:5555' -> (hote, port).
    'wss://abc.trycloudflare.com' ou 'abc.trycloudflare.com' -> voie HTTPS."""
    text = (text or "").strip()
    low = text.lower()
    for prefix in ("wss://", "https://", "http://"):
        if low.startswith(prefix):
            low = low[len(prefix):]
    web_host = low.split("/", 1)[0].split(":", 1)[0]
    if text.lower().startswith("wss://") or web_host.endswith(".trycloudflare.com"):
        return "wss://" + web_host, 443
    for prefix in ("tcp://", "http://", "https://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.rstrip("/")
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, _, port = text.partition(":")
    else:
        host, port = text, ""
    try:
        port = int(port) if port else int(default_port)
    except ValueError:
        port = int(default_port)
    return host.strip(), port


def is_private(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local


def connect_timeout(host):
    """Delai court pour les adresses de reseau local : si l'invite n'est pas sur
    le meme reseau, on passe vite a l'adresse suivante."""
    if host.startswith("100."):     # Tailscale (CGNAT) : peut etre lointain
        return 6.0
    return 1.2 if is_private(host) or host.startswith("127.") else 8.0
