"""Historique collaboratif (tenu par l'hote).

Bien plus riche que l'annulation native de Blender :
- chaque entree garde l'etat AVANT et APRES de chaque champ modifie ;
- Ctrl+Z annule MA derniere action sans toucher au travail des autres ;
- n'importe quelle entree peut etre annulee individuellement ;
- "Revenir a ce point" restaure l'etat exact de la scene a cet instant ;
- un glisser (G/R/S) produit une seule entree (fusion des micro-etapes) ;
- conflit : si quelqu'un a modifie le meme champ depuis, ce champ est
  preserve et l'utilisateur est prevenu ;
- journal JSONL sur disque + payloads persistants.
"""

import copy
import json
import os
import time

from . import ids, payloads

MAX_ENTRIES = 100000   # garde-fou absolu ; la limite reglable est history_limit
MERGE_WINDOW = 1.2

KIND_LABELS = {"op": "", "undo": "Annuler", "redo": "Retablir", "revert": "Retour",
               "restore": "Restauration", "checkpoint": "Point de sauvegarde",
               "join": "Arrivee", "leave": "Depart"}

_TRANSFORM = {"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle",
              "scale", "delta_location", "delta_rotation_euler", "delta_scale",
              "delta_rotation_quaternion"}


def describe(changes, op_label=None, known=None):
    """Libelle lisible d'un ensemble de changements."""
    if not changes:
        return op_label or "Modification"
    names = []
    for c in changes:
        n = c.get("n") or (c.get("f") or {}).get("name")
        if not n and known is not None:
            n = known.get(c["u"], {}).get("f", {}).get("name")
        if n and n not in names and c["t"] in ("objects", "materials", "collections", "scenes",
                                                "node_groups", "worlds", "actions", "images"):
            names.append(n)
    if not names:
        for c in changes:
            n = c.get("n") or (c.get("f") or {}).get("name")
            if n and n not in names:
                names.append(n)
    target = ", ".join(names[:2]) + (" +%d" % (len(names) - 2) if len(names) > 2 else "")
    if op_label:
        return "%s : %s" % (op_label, target) if target else op_label
    kinds = {c["k"] for c in changes}
    if kinds == {"c"}:
        main = next((c for c in changes if c["t"] == "objects"), changes[0])
        return "Ajout %s : %s" % (ids.label_of(main["t"]), target)
    if kinds == {"d"}:
        return "Suppression : %s" % target
    fields = set()
    for c in changes:
        fields.update((c.get("f") or {}).keys())
    if fields and fields <= _TRANSFORM:
        verb = "Transformation"
    elif "_px" in fields:
        verb = "Peinture de texture"
    elif "fsig" in fields:
        verb = "Texture mise a jour"
    elif "geom" in fields or ("fp" in fields and any(c["t"] == "meshes" for c in changes)):
        verb = "Edition du maillage"
    elif "_mods" in fields:
        verb = "Modificateurs"
    elif "_cons" in fields:
        verb = "Contraintes"
    elif fields == {"name"}:
        verb = "Renommage"
    elif "_objects" in fields or "_children" in fields:
        verb = "Organisation des collections"
    elif "fp" in fields:
        verb = "Modification %s" % ids.label_of(changes[0]["t"])
    elif "_hidden" in fields or "hide_viewport" in fields or "hide_render" in fields:
        verb = "Visibilite"
    else:
        verb = "Modification"
    return "%s : %s" % (verb, target) if target else verb


def _sig(entry):
    return tuple(sorted((c["u"], tuple(sorted((c.get("a") or {}).keys())))
                        for c in entry["changes"]))


class History:
    def __init__(self, session_dir, store):
        self.entries = []
        self.by_seq = {}
        self.seq = 0
        self.store = store
        self.redo = {}  # user -> [seq]
        self.path = os.path.join(session_dir, "history.jsonl") if session_dir else None
        self._unflushed = None

    # ------------------------------------------------------------------
    def summary(self, e):
        return {"s": e["seq"], "u": e["user"], "n": e["uname"], "c": e["color"],
                "t": e["time"], "l": e["label"], "x": e["undone"], "k": e["kind"],
                "nc": len(e["changes"])}

    def summaries(self, limit=300):
        return [self.summary(e) for e in self.entries[-limit:]]

    def _persist_refs(self, obj):
        for r in payloads.collect_refs(obj):
            self.store.persist(r)

    def _flush(self, e):
        if e is None:
            return
        self._persist_refs([c.get("a") for c in e["changes"]])
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def flush(self):
        self._flush(self._unflushed)
        self._unflushed = None

    def add(self, user, uname, color, changes, label, kind="op", ref=None, mergeable=True):
        """Ajoute une entree ; retourne (entree, fusionnee)."""
        now = time.time()
        entry = {"seq": 0, "user": user, "uname": uname, "color": color, "time": now,
                 "label": label, "kind": kind, "ref": ref, "undone": False, "changes": changes}
        last = self.entries[-1] if self.entries else None
        if (mergeable and last is not None and kind == "op" and last["kind"] == "op"
                and last["user"] == user and not last["undone"]
                and now - last["time"] < MERGE_WINDOW
                and all(c["k"] == "u" for c in changes)
                and all(c["k"] == "u" for c in last["changes"])
                and _sig(last) == _sig(entry)):
            by_uid = {c["u"]: c for c in last["changes"]}
            for c in changes:
                by_uid[c["u"]]["a"] = c["a"]
            last["time"] = now
            if label and not label.startswith(("Transformation", "Modification", "Edition")):
                last["label"] = label
            return last, True
        self.flush()
        self.seq += 1
        entry["seq"] = self.seq
        self._persist_refs([c.get("b") for c in changes])
        self.entries.append(entry)
        self.by_seq[entry["seq"]] = entry
        self._unflushed = entry
        if kind == "op":
            self.redo[user] = []
        while len(self.entries) > MAX_ENTRIES:
            old = self.entries.pop(0)
            self.by_seq.pop(old["seq"], None)
        return entry, False

    def expire(self, limit):
        """Oublie les actions les plus anciennes au-dela de `limit` (0 = jamais).

        Elles restent dans le journal history.jsonl sur le disque mais ne sont
        plus annulables : la memoire et les listes restent legeres. Retourne
        (nombre oublie, numero de la derniere action oubliee)."""
        if limit <= 0 or len(self.entries) <= limit:
            return 0, 0
        self.flush()
        n = len(self.entries) - limit
        gone = self.entries[:n]
        del self.entries[:n]
        for e in gone:
            self.by_seq.pop(e["seq"], None)
        gone_seqs = {e["seq"] for e in gone}
        for user in list(self.redo):
            self.redo[user] = [s for s in self.redo[user] if s not in gone_seqs]
        return n, gone[-1]["seq"]

    # ------------------------------------------------------------------
    def last_undoable(self, user):
        for e in reversed(self.entries):
            if e["user"] == user and e["kind"] in ("op", "revert", "redo") and not e["undone"]:
                if e["kind"] == "redo":
                    # un "retablir" se defait en re-annulant l'entree d'origine
                    continue
                return e
        return None

    def push_redo(self, user, seq):
        self.redo.setdefault(user, []).append(seq)

    def pop_redo(self, user):
        stack = self.redo.get(user) or []
        while stack:
            seq = stack.pop()
            e = self.by_seq.get(seq)
            if e is not None and e["undone"]:
                return e
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _same(a, b):
        return a == b

    def inverse(self, entry, known, check=True):
        """Changements qui defont l'entree. Retourne (changements, nb ignores)."""
        out = []
        skipped = 0
        for c in reversed(entry["changes"]):
            uid, attr = c["u"], c["t"]
            cur = known.get(uid)
            if c["k"] == "u":
                if cur is None:
                    skipped += 1
                    continue
                f = {}
                before = c.get("b") or {}
                for k, av in (c.get("a") or {}).items():
                    if k in ("blob", "file"):
                        continue
                    if check and not self._same(cur["f"].get(k), av):
                        skipped += 1
                        continue
                    if k in before:
                        f[k] = copy.deepcopy(before[k])
                if "fp" in f:
                    if "blob" in before:
                        f["blob"] = before["blob"]
                        if "file" in before:
                            f["file"] = before["file"]
                    else:
                        f.pop("fp")
                if f:
                    out.append({"u": uid, "t": attr, "k": "u", "f": f})
            elif c["k"] == "c":
                if cur is not None:
                    out.append({"u": uid, "t": attr, "k": "d", "n": cur["f"].get("name", "")})
            elif c["k"] == "d":
                if cur is None and c.get("b"):
                    out.append({"u": uid, "t": attr, "k": "c", "f": copy.deepcopy(c["b"])})
        return out, skipped

    def forward(self, entry, known, check=True):
        """Changements qui re-appliquent l'entree (retablir)."""
        out = []
        skipped = 0
        for c in entry["changes"]:
            uid, attr = c["u"], c["t"]
            cur = known.get(uid)
            if c["k"] == "u":
                if cur is None:
                    skipped += 1
                    continue
                f = {}
                before = c.get("b") or {}
                for k, av in (c.get("a") or {}).items():
                    if k in ("blob", "file"):
                        continue
                    if check and k in before and not self._same(cur["f"].get(k), before[k]):
                        skipped += 1
                        continue
                    f[k] = copy.deepcopy(av)
                if "fp" in f:
                    a = c.get("a") or {}
                    if "blob" in a:
                        f["blob"] = a["blob"]
                        if "file" in a:
                            f["file"] = a["file"]
                    else:
                        f.pop("fp")
                if f:
                    out.append({"u": uid, "t": attr, "k": "u", "f": f})
            elif c["k"] == "c":
                if cur is None and c.get("a"):
                    out.append({"u": uid, "t": attr, "k": "c", "f": copy.deepcopy(c["a"])})
            elif c["k"] == "d":
                if cur is not None:
                    out.append({"u": uid, "t": attr, "k": "d", "n": cur["f"].get("name", "")})
        return out, skipped

    def state_at(self, seq, known):
        """Changements ramenant la scene a l'etat juste apres l'entree `seq`."""
        target = {}
        for e in self.entries:
            if e["seq"] <= seq:
                continue
            for c in e["changes"]:
                uid = c["u"]
                cur = target.get(uid)
                if c["k"] == "c":
                    if cur is None:
                        target[uid] = ["absent", c["t"], None]
                elif c["k"] == "d":
                    full = copy.deepcopy(c.get("b") or {})
                    if cur is None:
                        target[uid] = ["full", c["t"], full]
                    elif cur[0] == "fields":
                        full.update(cur[2])
                        target[uid] = ["full", c["t"], full]
                else:
                    b = c.get("b") or {}
                    if cur is None:
                        target[uid] = ["fields", c["t"], copy.deepcopy(b)]
                    elif cur[0] == "fields":
                        for k, v in b.items():
                            cur[2].setdefault(k, copy.deepcopy(v))
        out = []
        for uid, (state, attr, fields) in target.items():
            now = known.get(uid)
            if state == "absent":
                if now is not None:
                    out.append({"u": uid, "t": attr, "k": "d", "n": now["f"].get("name", "")})
                continue
            if now is None:
                if state == "full" and fields:
                    out.append({"u": uid, "t": attr, "k": "c", "f": fields})
                continue
            f = {}
            for k, v in fields.items():
                if k in ("blob", "file"):
                    continue
                if now["f"].get(k) != v:
                    f[k] = v
            if "fp" in f:
                if "blob" in fields:
                    f["blob"] = fields["blob"]
                    if "file" in fields:
                        f["file"] = fields["file"]
                else:
                    f.pop("fp")
            if f:
                out.append({"u": uid, "t": attr, "k": "u", "f": f})
        return out
