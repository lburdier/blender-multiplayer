"""Valeurs pilotees par des drivers ou une animation : a ne pas diffuser.

Chaque joueur calcule lui-meme les drivers et l'animation : les envoyer ferait
des allers-retours inutiles (et des echos). Mais seule la valeur pilotee est
ignoree, pas tout le champ : sur un rig dont les roues ont des drivers, un os
deplace a la main ou par un addon (conduite au clavier...) et les proprietes
personnalisees des os (feux, reglages) doivent toujours etre envoyes.

Aucun appel a bpy ici.
"""

import re

_Q = r'"((?:[^"\\]|\\.)*)"'
_BONE = re.compile(r'^pose\.bones\[' + _Q + r'\](.*)$')
_ITEM = re.compile(r'^(modifiers|constraints)\[' + _Q + r'\](.*)$')
_PROP = re.compile(r'^\[' + _Q + r'\]$')

# position des canaux dans le format de pose de serialize.read_object
POSE_CHANNELS = {"location": 0, "rotation_quaternion": 2, "rotation_euler": 3,
                 "rotation_axis_angle": 4, "scale": 5}
POSE_PROPS = 6
_FIELD = {"modifiers": "_mods", "constraints": "_cons"}


def _unquote(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def split(paths):
    """{chemin RNA: {index}} -> (champs entierement pilotes, masques par champ).

    Masques : "_pose" {os: {(canal, index) ou (6, propriete)}},
    "_props" {propriete}, "_mods" / "_cons" {nom: {("p", reglage) ou ("i", entree)}}.
    """
    fields, masks = set(), {}
    for path, idxs in paths.items():
        m = _BONE.match(path)
        if m:
            bone, rest = _unquote(m.group(1)), m.group(2)
            bm = masks.setdefault("_pose", {}).setdefault(bone, set())
            if rest.startswith("."):
                ch = POSE_CHANNELS.get(rest[1:])
                if ch is not None:
                    bm.update((ch, i) for i in idxs)
            else:
                p = _PROP.match(rest)
                if p:
                    bm.add((POSE_PROPS, _unquote(p.group(1))))
            continue
        m = _ITEM.match(path)
        if m:
            name, rest = _unquote(m.group(2)), m.group(3)
            im = masks.setdefault(_FIELD[m.group(1)], {}).setdefault(name, set())
            if rest.startswith("."):
                im.add(("p", rest[1:].split(".")[0].split("[")[0]))
            else:
                p = _PROP.match(rest)
                if p:
                    im.add(("i", _unquote(p.group(1))))
            continue
        p = _PROP.match(path)
        if p:
            masks.setdefault("_props", set()).add(_unquote(p.group(1)))
            continue
        head = path.split(".")[0].split("[")[0]
        if head and head != "data":
            fields.add(head)
    return fields, masks


def _dict_equal(a, b, skip):
    a, b = a or {}, b or {}
    return all(a.get(k) == b.get(k) for k in set(a) | set(b) if k not in skip)


def equal(field, cur, known, mask):
    """cur == known en ignorant les valeurs pilotees decrites par `mask`."""
    if cur == known:
        return True
    if known is None or not mask:
        return False
    if field == "_pose":
        if not isinstance(known, dict) or cur.keys() != known.keys():
            return False
        for bone, vals in cur.items():
            kv = known[bone]
            if vals == kv:
                continue
            bm = mask.get(bone)
            if not bm or len(vals) != len(kv):
                return False
            for i, (a, b) in enumerate(zip(vals, kv)):
                if a == b:
                    continue
                if i == POSE_PROPS:
                    if not _dict_equal(a, b, {k for c, k in bm if c == POSE_PROPS}):
                        return False
                elif not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
                    return False
                elif any(x != y and (i, j) not in bm for j, (x, y) in enumerate(zip(a, b))):
                    return False
        return True
    if field == "_props":
        return isinstance(known, dict) and _dict_equal(cur, known, mask)
    if field in ("_mods", "_cons"):
        if not isinstance(known, list) or len(cur) != len(known):
            return False
        for a, b in zip(cur, known):
            if a == b:
                continue
            if not isinstance(a, dict) or not isinstance(b, dict) or a.get("n") != b.get("n"):
                return False
            im = mask.get(a.get("n"), ())
            if not _dict_equal(a.get("p"), b.get("p"), {k for c, k in im if c == "p"}):
                return False
            if not _dict_equal(a.get("i"), b.get("i"), {k for c, k in im if c == "i"}):
                return False
            if any(a.get(k) != b.get(k) for k in (set(a) | set(b)) - {"p", "i"}):
                return False
        return True
    return False
