"""Mises a jour automatiques de l'addon.

Le depot d'extensions du projet (GitHub Pages) publie un index au format
officiel de Blender :
    https://lburdier.github.io/blender-multiplayer/index.json

- Ajoute comme "depot distant" dans Blender, il donne les mises a jour natives
  (Preferences > Get Extensions).
- Quel que soit le mode d'installation, l'addon verifie aussi lui-meme cet
  index au demarrage et peut installer la nouvelle version tout seul (quand
  aucune session n'est en cours) ou d'un clic.

Respecte le reglage "Autoriser l'acces en ligne" de Blender.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

import bpy

from . import compat

PKG_ID = "blender_multiplayer"
INDEX_URL = os.environ.get("MP_UPDATE_INDEX") or "https://lburdier.github.io/blender-multiplayer/index.json"
REPO_URL = "https://github.com/lburdier/blender-multiplayer"
RELEASES_URL = REPO_URL + "/releases/latest"
_UA = "Blender-Multiplayer-Updater"

state = {"status": "idle", "latest": "", "entry": None, "error": "", "checked": 0.0,
         "auto": False, "download": None}
_lock = threading.Lock()


def _vtuple(text):
    out = []
    for part in str(text).split("."):
        num = ""
        for ch in part:
            if not ch.isdigit():
                break
            num += ch
        out.append(int(num or 0))
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def current_version():
    return tuple(compat.ADDON_VERSION)


def current_version_str():
    return ".".join(str(x) for x in current_version())


def online_allowed():
    return bool(getattr(bpy.app, "online_access", True))


def install_kind():
    """'local' (installe depuis un zip), 'remote' (depot distant) ou 'legacy'."""
    parts = (__package__ or "").split(".")
    if len(parts) < 3 or parts[0] != "bl_ext":
        return "legacy", None, -1
    module = parts[1]
    for i, repo in enumerate(bpy.context.preferences.extensions.repos):
        if repo.module == module:
            return ("remote" if repo.use_remote_url else "local"), module, i
    return "legacy", None, -1


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _fetch_index():
    req = urllib.request.Request(INDEX_URL, headers={"User-Agent": _UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read(2 * 1024 * 1024).decode("utf-8"))
    best = None
    app = tuple(bpy.app.version)
    for entry in data.get("data", []):
        if entry.get("id") != PKG_ID:
            continue
        if _vtuple(entry.get("blender_version_min", "0")) > app:
            continue
        vmax = entry.get("blender_version_max")
        if vmax and app >= _vtuple(vmax):
            continue
        if best is None or _vtuple(entry["version"]) > _vtuple(best["version"]):
            best = entry
    if best is not None:
        best = dict(best)
        best["archive_url"] = urllib.parse.urljoin(INDEX_URL, best.get("archive_url", ""))
    return best


def _check_thread():
    try:
        entry = _fetch_index()
        with _lock:
            state["checked"] = time.time()
            if entry is not None and current_version() != (0, 0, 0) and \
                    _vtuple(entry["version"]) > current_version():
                state.update(status="available", latest=entry["version"], entry=entry, error="")
            else:
                state.update(status="uptodate", latest=entry["version"] if entry else "",
                             entry=entry, error="")
    except Exception as exc:  # noqa: BLE001
        with _lock:
            state.update(status="error", error=str(exc)[:160], checked=time.time())


def check(auto_install=False):
    """Lance une verification en arriere-plan (non bloquante)."""
    if state["status"] in ("checking", "downloading", "installing"):
        return
    if not online_allowed():
        state.update(status="offline")
        return
    state.update(status="checking", auto=auto_install, error="")
    threading.Thread(target=_check_thread, daemon=True, name="mp-update-check").start()
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.5, persistent=True)


def _poll():
    st = state["status"]
    if st in ("checking", "downloading"):
        return 0.5
    compat.tag_redraw()
    if st == "available" and state["auto"]:
        state["auto"] = False
        from . import session
        if session.current is None:
            install()
            return 0.5 if state["status"] == "downloading" else None
    if st == "downloaded":
        _install_downloaded()
    return None


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def _download_thread(entry):
    try:
        req = urllib.request.Request(entry["archive_url"], headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read(64 * 1024 * 1024)
        expected = str(entry.get("archive_hash", ""))
        if expected.startswith("sha256:") and hashlib.sha256(data).hexdigest() != expected[7:]:
            raise ValueError("empreinte du fichier incorrecte")
        size = entry.get("archive_size")
        if size and int(size) != len(data):
            raise ValueError("taille du fichier incorrecte")
        folder = tempfile.mkdtemp(prefix="mp_update_")
        path = os.path.join(folder, "%s-%s.zip" % (PKG_ID, entry["version"]))
        with open(path, "wb") as fh:
            fh.write(data)
        with _lock:
            state.update(status="downloaded", download=path)
    except Exception as exc:  # noqa: BLE001
        with _lock:
            state.update(status="error", error="telechargement : %s" % str(exc)[:140])


def install():
    """Installe la derniere version. Renvoie un message d'erreur ou ''."""
    from . import session
    if session.current is not None:
        return "Quittez la session avant de mettre a jour."
    entry = state.get("entry")
    if not entry or state["status"] not in ("available", "error"):
        return "Aucune mise a jour disponible."
    kind, module, index = install_kind()
    if kind == "legacy":
        webbrowser.open(RELEASES_URL)
        return "Installation classique : telechargez la nouvelle version sur la page qui vient de s'ouvrir."
    if kind == "remote":
        state["status"] = "installing"
        bpy.app.timers.register(lambda: _install_remote(index), first_interval=0.1, persistent=True)
        return ""
    state["status"] = "downloading"
    threading.Thread(target=_download_thread, args=(entry,), daemon=True, name="mp-update-dl").start()
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=0.5, persistent=True)
    return ""


def _run_op(op, **kw):
    res = compat.call_op(op, **kw)
    if "FINISHED" not in res:
        raise RuntimeError("Blender a refuse l'installation (%s)" % ", ".join(sorted(res)))


def _mark_updated():
    os.environ["MP_UPDATED_FROM"] = "%s>%s" % (current_version_str(), state.get("latest", ""))


def updated_from():
    """('1.0.0', '1.1.0') si l'addon vient d'etre mis a jour dans ce Blender."""
    val = os.environ.get("MP_UPDATED_FROM", "")
    return tuple(val.split(">", 1)) if ">" in val else None


def _finish_error(exc):
    os.environ.pop("MP_UPDATED_FROM", None)
    state.update(status="error", error=str(exc)[:160])
    compat.tag_redraw()


def _install_remote(index):
    # Execute depuis un timer : le module peut etre recharge pendant l'appel.
    try:
        _mark_updated()
        _run_op(bpy.ops.extensions.repo_sync, repo_index=index)
        _run_op(bpy.ops.extensions.package_install, repo_index=index, pkg_id=PKG_ID,
                enable_on_install=True)
        state["status"] = "installed"
    except Exception as exc:  # noqa: BLE001
        _finish_error(exc)
    return None


def _install_downloaded():
    path = state.get("download")
    _kind, module, _index = install_kind()
    state["status"] = "installing"
    try:
        _mark_updated()
        _run_op(bpy.ops.extensions.package_install_files, filepath=path, repo=module,
                enable_on_install=True, overwrite=True)
        state["status"] = "installed"
    except Exception as exc:  # noqa: BLE001
        _finish_error(exc)


# ---------------------------------------------------------------------------

def _startup():
    from . import prefs
    p = prefs.get()
    if getattr(p, "check_updates", True) and online_allowed():
        check(auto_install=getattr(p, "auto_update", True))
    return None


def register():
    if bpy.app.background or os.environ.get("MP_NO_UPDATE_CHECK"):
        return
    # persistent : sinon Blender supprime le timer en chargeant le .blend passe au
    # lancement (double-clic sur un fichier) et la verification ne se fait jamais.
    bpy.app.timers.register(_startup, first_interval=4.0, persistent=True)


def unregister():
    for fn in (_startup, _poll):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
