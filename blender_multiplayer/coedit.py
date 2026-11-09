"""Edition a plusieurs du meme maillage, en mode Edition, en temps reel.

Avant, entrer en mode Edition posait un verrou : les autres ne pouvaient ni
entrer dans le meme objet, ni le modifier. Maintenant plusieurs joueurs peuvent
etre en mode Edition sur le meme maillage :

- les sommets deplaces par un autre arrivent en direct dans le BMesh de celui
  qui edite aussi (live.py appelle `apply_live`) ;
- l'etat complet (fin de geste, extrusion, suppression, UV...) est fusionne :
  * meme topologie et memes attributs : seuls les sommets que l'autre a
    deplaces (par rapport a l'etat de reference commun) sont repris, ceux que
    l'on deplace soi-meme restent a leur place ;
  * topologie differente : le BMesh est remplace par celui recu, en gardant la
    selection (par index, ou par position si le nombre de sommets change).
    Jamais pendant une operation interactive locale (deplacement G, couteau...)
    qui garde des pointeurs vers les sommets : on reessaie juste apres.

Chaque joueur renvoie son etat s'il differe de l'etat de reference : les
editions croisees convergent vers le meme maillage partout.
"""

import bmesh
import bpy
import numpy as np

from . import ids, payloads

# Operateurs interactifs qui tiennent des pointeurs vers les elements du BMesh.
_BUSY_PREFIXES = ("TRANSFORM_OT", "MESH_OT", "VIEW3D_OT", "SCULPT_OT", "UV_OT", "WM_OT_tool",
                  "GIZMOGROUP_OT", "PAINT_OT", "OBJECT_OT_vertex")


def busy():
    """Vrai si une operation interactive locale est en cours."""
    try:
        for win in bpy.context.window_manager.windows:
            for op in getattr(win, "modal_operators", ()):
                if op.bl_idname.startswith(_BUSY_PREFIXES):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def edit_owner(me):
    for o in bpy.data.objects:
        try:
            if o.data == me and o.mode == "EDIT":
                return o
        except ReferenceError:
            continue
    return None


def _same_layout(lm, la, sm, sa):
    if (lm["nv"], lm["ne"], lm["nl"], lm["np"]) != (sm["nv"], sm["ne"], sm["nl"], sm["np"]):
        return False
    for k in ("edges", "loop_v", "poly_start"):
        if not np.array_equal(la[k], sa[k]):
            return False
    return True


def _same_attrs(lm, la, sm, sa):
    if sorted(map(tuple, lm.get("attrs", []))) != sorted(map(tuple, sm.get("attrs", []))):
        return False
    for k, v in sa.items():
        if k.startswith("a:") and not np.array_equal(la.get(k), v):
            return False
    for k in ("uv", "col", "colr", "uvr"):
        if lm.get(k) != sm.get(k):
            return False
    return True


def _known_base(uid, ctx):
    """Positions de l'etat de reference commun (dernier etat complet accepte)."""
    try:
        ref = ctx.known.get(uid, {}).get("f", {}).get("geom")
        data = ctx.store.get(ref["$p"]) if ref else None
        if data is None:
            return None
        _m, arrays, _r = payloads.unpack(data)
        return arrays["co"].reshape(-1, 3)
    except Exception:  # noqa: BLE001
        return None


def merge(me, src, ctx):
    """Applique le maillage `src` (hors mode Edition) au maillage `me`, en
    mode Edition chez nous, sans en sortir. Leve serialize.Unresolved pour
    reessayer plus tard si c'est impossible maintenant."""
    from . import serialize
    owner = edit_owner(me)
    if owner is None:
        raise serialize.Unresolved(me.name)
    uid = ids.get_uid(me)
    owner.update_from_editmode()
    lm, la, _r = payloads.unpack(serialize.geometry_bytes(me))
    sm, sa, _r = payloads.unpack(serialize._geometry_bytes(src))
    keys = me.shape_keys is not None or src.shape_keys is not None
    live = getattr(ctx, "live", None)
    if not keys and _same_layout(lm, la, sm, sa) and _same_attrs(lm, la, sm, sa):
        new = sa["co"].reshape(-1, 3)
        local = la["co"].reshape(-1, 3)
        base = _known_base(uid, ctx)
        if base is None or base.shape != new.shape:
            base = local
        idx = np.flatnonzero(np.any(new != base, axis=1) & np.any(new != local, axis=1))
        if idx.size:
            bm = bmesh.from_edit_mesh(me)
            bm.verts.ensure_lookup_table()
            verts = bm.verts
            for i, co in zip(idx.tolist(), new[idx].tolist()):
                verts[i].co = co
            bmesh.update_edit_mesh(me, loop_triangles=True, destructive=False)
            if live is not None:
                st = live.mesh_tx.get(uid)
                if st is not None and st.base is not None and st.base.size == new.size:
                    st.base.reshape(-1, 3)[idx] = new[idx]
        return
    if busy() or (live is not None and live.tx_hold.get(uid, 0.0) > _now()):
        raise serialize.Unresolved(me.name)
    replace(me, src, owner)
    if live is not None:
        live.mesh_tx.pop(uid, None)


def _now():
    import time
    return time.time()


def replace(me, src, owner):
    """Remplace le BMesh d'edition par `src` en gardant selection et masquage."""
    from mathutils import kdtree
    bm = bmesh.from_edit_mesh(me)
    old_n = len(bm.verts)
    sel = [v.select for v in bm.verts]
    hid = [v.hide for v in bm.verts]
    picked = [v.co.copy() for v in bm.verts if v.select]
    select_mode = set(bm.select_mode)
    key_index = 0
    if src.shape_keys is not None:
        name = owner.active_shape_key.name if owner.active_shape_key is not None else ""
        key_index = max(0, src.shape_keys.key_blocks.find(name)) if name else 0
    bm.clear()
    bm.from_mesh(src, use_shape_key=src.shape_keys is not None, shape_key_index=key_index)
    bm.verts.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    if len(bm.verts) == old_n:
        for v, s, h in zip(bm.verts, sel, hid):
            v.select = s
            v.hide = h
    elif picked:
        tree = kdtree.KDTree(len(picked))
        for i, co in enumerate(picked):
            tree.insert(co, i)
        tree.balance()
        for v in bm.verts:
            v.select = tree.find(v.co)[2] <= 1e-5
    bm.select_mode = select_mode
    bm.select_flush_mode()
    bmesh.update_edit_mesh(me, loop_triangles=True, destructive=True)


def merge_bytes(me, data, ctx):
    """Geometrie recue (octets de serialize.geometry_bytes) -> maillage en edition."""
    from . import serialize
    tmp = bpy.data.meshes.new("__mp_coedit")
    try:
        serialize.apply_geometry(tmp, data)
        merge(me, tmp, ctx)
    finally:
        bpy.data.meshes.remove(tmp)


def apply_live(me, items, live, uid):
    """Sommets deplaces en direct par un autre -> BMesh d'edition local."""
    owner = edit_owner(me)
    if owner is None:
        return
    bm = bmesh.from_edit_mesh(me)
    bm.verts.ensure_lookup_table()
    verts = bm.verts
    nv = len(verts)
    keys = me.shape_keys
    active = owner.active_shape_key
    st = live.mesh_tx.get(uid)
    changed = False
    for n, name, idx, co in items:
        if n != nv:
            continue        # topologie differente : l'etat complet arrive
        layer = None
        if keys is not None and name and (active is None or active.name != name):
            layer = bm.verts.layers.shape.get(name)
            if layer is None:
                continue
        if idx is None:
            idx_l = range(nv)
            co_l = co.reshape(-1, 3).tolist()
        else:
            idx_l = idx.tolist()
            co_l = co.tolist()
        if layer is None:
            for i, c in zip(idx_l, co_l):
                verts[i].co = c
        else:
            for i, c in zip(idx_l, co_l):
                verts[i][layer] = c
        changed = True
        if layer is None and st is not None and st.base is not None and st.base.size == nv * 3:
            # Ces sommets viennent d'un autre : ne pas les renvoyer comme les notres.
            if idx is None:
                st.base[:] = co.reshape(-1)
            else:
                st.base.reshape(-1, 3)[idx] = co
    if changed:
        bmesh.update_edit_mesh(me, loop_triangles=True, destructive=False)
