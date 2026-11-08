"""Stockage adresse-par-contenu des donnees lourdes (geometrie, blobs .blend).

Les etats des data-blocks ne contiennent qu'une reference {"$p": sha1} ;
les octets voyagent en piece jointe des messages et sont conserves ici.
Chez l'hote, ils sont aussi ecrits sur disque : l'historique peut ainsi
revenir a n'importe quel etat passe, meme apres de longues sessions.
"""

import hashlib
import json
import os
import struct
from collections import OrderedDict

import numpy as np


def sha(data):
    return hashlib.sha1(data).hexdigest()


class PayloadStore:
    def __init__(self, disk_dir=None, mem_limit=384 * 1024 * 1024):
        self.disk_dir = disk_dir
        self.mem_limit = mem_limit
        self._mem = OrderedDict()
        self._size = 0
        if disk_dir:
            os.makedirs(disk_dir, exist_ok=True)

    def _disk_path(self, key):
        return os.path.join(self.disk_dir, key[:2], key + ".bin")

    def put(self, data, key=None, persist=False):
        key = key or sha(data)
        if key in self._mem:
            self._mem.move_to_end(key)
        else:
            self._mem[key] = data
            self._size += len(data)
        if persist:
            self.persist(key)
        while self._size > self.mem_limit and len(self._mem) > 1:
            _k, v = self._mem.popitem(last=False)
            self._size -= len(v)
        return key

    def persist(self, key):
        """Ecrit le payload sur disque (hote) pour l'historique long terme."""
        if not self.disk_dir:
            return False
        path = self._disk_path(key)
        if os.path.exists(path):
            return True
        data = self._mem.get(key)
        if data is None:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return True

    def get(self, key):
        data = self._mem.get(key)
        if data is not None:
            self._mem.move_to_end(key)
            return data
        if self.disk_dir:
            path = self._disk_path(key)
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    data = fh.read()
                self._mem[key] = data
                self._size += len(data)
                return data
        return None

    def has(self, key):
        return key in self._mem or bool(self.disk_dir and os.path.exists(self._disk_path(key)))


# ---------------------------------------------------------------------------
# Pieces jointes de messages
# ---------------------------------------------------------------------------

def collect_refs(obj, out=None):
    """Toutes les references {"$p": sha} contenues dans une structure JSON."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        p = obj.get("$p")
        if isinstance(p, str) and len(obj) == 1:
            out.append(p)
        else:
            for v in obj.values():
                collect_refs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_refs(v, out)
    return out


def attach(msg, store, extra_refs=()):
    """Ajoute au message les octets des payloads references. Retourne le binaire."""
    refs = []
    seen = set()
    for r in list(collect_refs(msg)) + list(extra_refs):
        if r not in seen:
            seen.add(r)
            refs.append(r)
    parts = []
    table = []
    for r in refs:
        data = store.get(r)
        if data is None:
            continue
        table.append([r, len(data)])
        parts.append(data)
    if table:
        msg["att"] = table
    return b"".join(parts)


def detach(msg, binary, store):
    table = msg.pop("att", None)
    if not table:
        return
    pos = 0
    for key, length in table:
        store.put(binary[pos:pos + length], key=key)
        pos += length


# ---------------------------------------------------------------------------
# Conteneur de tableaux numpy (geometrie) et de blobs
# ---------------------------------------------------------------------------

_MAGIC = b"MPA1"


def pack(meta, arrays=None, raw=b""):
    """Format deterministe : meme contenu -> memes octets -> meme sha."""
    arrays = arrays or {}
    table = []
    blobs = []
    offset = 0
    for name, arr in arrays.items():
        arr = np.ascontiguousarray(arr)
        data = arr.tobytes()
        table.append([name, arr.dtype.str, list(arr.shape), offset, len(data)])
        blobs.append(data)
        offset += len(data)
    header = json.dumps({"meta": meta, "arrays": table, "raw": len(raw)},
                        separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _MAGIC + struct.pack("!I", len(header)) + header + b"".join(blobs) + raw


def unpack(data):
    if data[:4] != _MAGIC:
        raise ValueError("payload invalide")
    (hl,) = struct.unpack("!I", data[4:8])
    header = json.loads(data[8:8 + hl].decode("utf-8"))
    base = 8 + hl
    arrays = {}
    end = base
    for name, dtype, shape, offset, length in header["arrays"]:
        start = base + offset
        arr = np.frombuffer(data, dtype=np.dtype(dtype), count=length // np.dtype(dtype).itemsize,
                            offset=start)
        arrays[name] = arr.reshape(shape) if shape else arr
        end = max(end, start + length)
    raw_len = header.get("raw", 0)
    raw = data[end:end + raw_len] if raw_len else b""
    return header["meta"], arrays, raw
