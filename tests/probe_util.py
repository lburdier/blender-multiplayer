"""Compteurs : mises a jour du depsgraph et messages, pendant une periode d'inactivite."""
import collections

import bpy

STATS = {"dg": 0, "ids": collections.Counter(), "msg": collections.Counter(),
         "fields": collections.Counter(), "sent": collections.Counter()}
ON = [False]


def _dg(scene, depsgraph):
    if not ON[0]:
        return
    STATS["dg"] += 1
    for u in depsgraph.updates:
        idb = u.id
        flags = "".join(c for c, f in (("T", u.is_updated_transform), ("G", u.is_updated_geometry),
                                         ("S", u.is_updated_shading)) if f)
        STATS["ids"]["%s:%s:%s" % (type(idb).__name__, idb.name[:30], flags)] += 1


def install(s):
    bpy.app.handlers.depsgraph_update_post.append(_dg)
    orig_handle = s._handle

    def handle(*a):
        if ON[0]:
            msg = a[-2] if len(a) >= 2 else a[0]
            t = msg.get("type")
            STATS["msg"][t] += 1
            for c in msg.get("ch", []) or []:
                for k in (c.get("f") or {}):
                    STATS["fields"]["%s.%s" % (c.get("t"), k)] += 1
        return orig_handle(*a)
    s._handle = handle
    orig_send = getattr(s, "send", None) or getattr(s, "broadcast")
    name = "send" if hasattr(s, "send") and not hasattr(s, "broadcast") else "broadcast"
    orig = getattr(s, name)

    def send(msg, *a, **k):
        if ON[0]:
            STATS["sent"][msg.get("type")] += 1
            for c in msg.get("ch", []) or []:
                for f in (c.get("f") or {}):
                    STATS["fields"]["SENT %s.%s" % (c.get("t"), f)] += 1
        return orig(msg, *a, **k)
    setattr(s, name, send)


def report():
    return {"dg": STATS["dg"], "ids": STATS["ids"].most_common(15), "msg": dict(STATS["msg"]),
            "sent": dict(STATS["sent"]), "fields": STATS["fields"].most_common(15)}
