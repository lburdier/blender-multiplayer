"""Cache des fichiers externes (textures...) envoyes avec la scene.

A l'arrivee d'un invite, l'hote annonce d'abord la liste de ses fichiers
(empreinte sha256, taille, nom, chemin). L'invite repond avec ceux qu'il
possede deja :
- dans son cache (recus lors d'une session precedente) ;
- au meme chemin sur son disque (projet copie ou synchronise sur les deux PC,
  ou deux Blender sur le meme PC), contenu verifie par l'empreinte ; il en
  fait alors une copie dans son cache.
L'hote n'envoie ensuite que les fichiers manquants.

Aucun appel a bpy ici.
"""

import hashlib
import os
import re
import shutil

CACHE_LIMIT = 4 * 1024 ** 3        # au-dela, les fichiers les plus anciens sont supprimes

_hashes = {}                       # (chemin, taille, mtime_ns) -> sha256


def file_sha(path):
    """Empreinte sha256 d'un fichier (memorisee tant qu'il ne change pas)."""
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    sha = _hashes.get(key)
    if sha is None:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        if len(_hashes) > 20000:
            _hashes.clear()
        _hashes[key] = sha
    return sha


def safe_name(fn):
    """Nom de fichier sans dossier ni caractere dangereux (il vient du reseau)."""
    fn = os.path.basename(str(fn or "").replace("\\", "/"))
    fn = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", fn).strip(" .")
    return fn[:120] or "fichier"


def cache_dir(root):
    path = os.path.join(root, "cache", "fichiers")
    os.makedirs(path, exist_ok=True)
    return path


def cached_path(root, sha, fn):
    return os.path.join(cache_dir(root), "%s_%s" % (str(sha)[:16], safe_name(fn)))


def _touch(path):
    try:
        os.utime(path, None)
    except OSError:
        pass


def find(root, sha, size, fn, path_hint=""):
    """Chemin local d'un fichier de contenu `sha`, ou None."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return None
    p = cached_path(root, sha, fn)
    try:
        if os.path.getsize(p) == size:
            _touch(p)
            return p
    except OSError:
        pass
    # Meme fichier au meme endroit sur ce PC : seulement si le nom correspond.
    # On en fait une copie locale (rapide, sans reseau) pour que l'invite ne
    # modifie jamais les fichiers d'origine de l'hote.
    if path_hint and os.path.basename(path_hint) == os.path.basename(str(fn)):
        try:
            if os.path.getsize(path_hint) == size and file_sha(path_hint) == sha:
                tmp = p + ".tmp"
                shutil.copyfile(path_hint, tmp)
                os.replace(tmp, p)
                return p
        except OSError:
            pass
    return None


def store(root, sha, fn, blob):
    """Enregistre un fichier recu dans le cache et renvoie son chemin."""
    p = cached_path(root, sha, fn)
    try:
        if os.path.getsize(p) == len(blob):
            _touch(p)
            return p
    except OSError:
        pass
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, p)
    return p


def trim(root, limit=CACHE_LIMIT, keep=()):
    """Supprime les fichiers les moins recemment utilises au-dela de `limit`."""
    folder = cache_dir(root)
    keep = {os.path.normcase(os.path.abspath(k)) for k in keep}
    files = []
    for name in os.listdir(folder):
        p = os.path.join(folder, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(f[1] for f in files)
    for _mtime, size, p in sorted(files):
        if total <= limit:
            break
        if os.path.normcase(os.path.abspath(p)) in keep:
            continue
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass
