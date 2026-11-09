"""Lecture et application de l'etat des data-blocks.

Un etat est un dict {champ: valeur JSON}. Chaque champ est l'unite de
fusion : deux joueurs qui modifient des champs differents du meme objet
(ex: position et couleur) ne s'ecrasent jamais.

Conventions de valeurs :
- reference vers un autre data-block : {"$r": uid} (ou {"$n","$a","$l"} pour
  une donnee liee d'une bibliotheque) ;
- donnees lourdes : {"$p": sha1} (octets transportes a part, cf payloads).
"""

import os

import bpy
import numpy as np

from . import compat, fingerprint, ids, payloads


class Unresolved(Exception):
    """Reference vers un data-block pas encore recu : on reessaiera."""


class Locked(Exception):
    """Le data-block est en cours d'edition locale (mode Edition...)."""


class MissingPayload(Exception):
    pass


# ---------------------------------------------------------------------------
# Valeurs
# ---------------------------------------------------------------------------

def _round(x):
    return round(float(x), 6)


def _flat(v):
    out = []
    for x in v:
        if hasattr(x, "__len__") and not isinstance(x, str):
            out.extend(_flat(x))
        else:
            out.append(x)
    return out


def _conv(val, p):
    t = p.type
    if t == "ENUM":
        return sorted(val) if p.is_enum_flag else val
    if t == "STRING":
        return val
    is_array = getattr(p, "is_array", None)
    if is_array is None:
        is_array = getattr(p, "array_length", 0) > 0
    if is_array:
        flat = _flat(val)
        if t == "FLOAT":
            return [_round(x) for x in flat]
        if t == "BOOLEAN":
            return [bool(x) for x in flat]
        return [int(x) for x in flat]
    if t == "FLOAT":
        return _round(val)
    if t == "BOOLEAN":
        return bool(val)
    return int(val)


def _prep(v, p):
    if p.type == "ENUM" and p.is_enum_flag:
        return set(v)
    if isinstance(v, list):
        dims = [d for d in getattr(p, "array_dimensions", (0,)) if d > 0]
        if len(dims) > 1 and len(v) == dims[0] * dims[1]:
            n = dims[1]
            return [v[i * n:(i + 1) * n] for i in range(dims[0])]
    return v


def ref(idb):
    if idb is None:
        return None
    try:
        if idb.library is None:
            u = ids.get_uid(idb)
            if u:
                return {"$r": u}
        lib = idb.library.filepath if idb.library else None
        return {"$n": idb.name, "$a": ids.attr_of_id(idb), "$l": lib}
    except ReferenceError:
        return None


def resolve_ref(v, ctx):
    if v is None:
        return None
    if not isinstance(v, dict):
        return None
    if "$r" in v:
        idb = ctx.resolve(v["$r"])
        if idb is None:
            raise Unresolved(v["$r"])
        return idb
    coll = getattr(bpy.data, v.get("$a") or "", None)
    if coll is None:
        return None
    lib = v.get("$l")
    for idb in coll:
        if idb.name == v.get("$n"):
            ipath = idb.library.filepath if idb.library else None
            if ipath == lib:
                return idb
    return coll.get(v.get("$n"))


def is_ref(v):
    return isinstance(v, dict) and ("$r" in v or "$n" in v)


# ---------------------------------------------------------------------------
# Lecture / ecriture generique par RNA
# ---------------------------------------------------------------------------

_GENERIC_SKIP = frozenset((
    "rna_type", "name", "name_full", "original", "users", "use_fake_user", "use_extra_user",
    "is_evaluated", "is_embedded_data", "is_missing", "is_runtime_data", "tag", "library",
    "library_weak_reference", "override_library", "preview", "asset_data", "session_uid",
    "id_type", "is_library_indirect", "animation_data", "is_editmode", "bl_rna",
    "show_expanded", "is_active", "is_override_data", "persistent_uid", "execution_time",
))


def read_struct(s, depth=1, skip=()):
    out = {}
    for p in s.bl_rna.properties:
        k = p.identifier
        if k in _GENERIC_SKIP or k in skip:
            continue
        t = p.type
        try:
            if t == "POINTER":
                if fingerprint.is_id_struct(p.fixed_type):
                    if p.is_readonly:
                        continue
                    out[k] = ref(getattr(s, k))
                elif p.is_readonly and depth > 0:
                    val = getattr(s, k)
                    if val is not None:
                        out[k] = read_struct(val, depth - 1)
                continue
            if t == "COLLECTION" or p.is_readonly:
                continue
            out[k] = _conv(getattr(s, k), p)
        except Exception:
            continue
    return out


def apply_prop(s, k, v, ctx):
    """Applique une propriete generique. Leve Unresolved si une ref manque."""
    p = s.bl_rna.properties.get(k)
    if p is None:
        return
    if p.type == "POINTER":
        if fingerprint.is_id_struct(p.fixed_type):
            target = resolve_ref(v, ctx)
            if getattr(s, k) != target:
                setattr(s, k, target)
        elif isinstance(v, dict):
            sub = getattr(s, k, None)
            if sub is not None:
                apply_struct(sub, v, ctx)
        return
    if p.is_readonly or p.type == "COLLECTION":
        return
    if _conv(getattr(s, k), p) == v:
        return
    setattr(s, k, _prep(v, p))


def apply_struct(s, d, ctx):
    unresolved = None
    failed = []
    for k, v in d.items():
        try:
            apply_prop(s, k, v, ctx)
        except Unresolved as exc:
            unresolved = exc
        except Exception:
            failed.append((k, v))
    # Certaines proprietes dependent d'autres (ex: type de lumiere) : 2e passe.
    for k, v in failed:
        try:
            apply_prop(s, k, v, ctx)
        except Exception:
            pass
    if unresolved is not None:
        raise unresolved


# ---------------------------------------------------------------------------
# Proprietes personnalisees (ID properties)
# ---------------------------------------------------------------------------

_NO = object()


def _conv_idprop(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return _round(v)
    if isinstance(v, str):
        return v
    if isinstance(v, bpy.types.ID):
        return ref(v)
    if hasattr(v, "to_list"):
        try:
            return [(_round(x) if isinstance(x, float) else x) for x in _flat(v.to_list())]
        except Exception:
            return _NO
    return _NO


def read_idprops(holder, rna=None):
    out = {}
    try:
        keys = list(holder.keys())
    except Exception:
        return out
    rna_props = rna.bl_rna.properties if rna is not None else None
    for k in keys:
        if k.startswith("_"):
            continue
        if rna_props is not None and k in rna_props:
            continue
        try:
            cv = _conv_idprop(holder[k])
        except Exception:
            continue
        if cv is not _NO:
            out[k] = cv
    return out


def apply_idprops(holder, d, ctx, remove_missing=True):
    unresolved = None
    for k, v in d.items():
        try:
            val = resolve_ref(v, ctx) if is_ref(v) else v
        except Unresolved as exc:
            unresolved = exc
            continue
        try:
            cur = holder.get(k, None)
            if cur is not None and _conv_idprop(cur) == v:
                continue
            holder[k] = val
        except Exception:
            pass
    if remove_missing:
        for k in list(read_idprops(holder).keys()):
            if k not in d:
                try:
                    del holder[k]
                except Exception:
                    pass
    if unresolved is not None:
        raise unresolved


# ---------------------------------------------------------------------------
# Animation (liaison d'une action a un data-block natif)
# ---------------------------------------------------------------------------

def read_anim(idb):
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return None
    act = ad.action
    slot = None
    if compat.has_rna_prop(ad, "action_slot"):
        try:
            s = ad.action_slot
            slot = s.identifier if s is not None else None
        except Exception:
            slot = None
    if act is None and slot is None:
        return None
    return {"a": ref(act), "s": slot}


# Cible d'une variable de driver : reglages lus et appliques s'ils existent
# (context_property n'existe qu'a partir de Blender 4.x).
_DRV_TARGET = ("id_type", "data_path", "bone_target", "transform_type", "transform_space",
               "rotation_mode", "context_property")


def _read_driver_vars(d):
    out = []
    for v in d.variables:
        targets = []
        for t in v.targets:
            td = {"id": ref(t.id)}
            for k in _DRV_TARGET:
                if hasattr(t, k):
                    td[k] = getattr(t, k)
            targets.append(td)
        out.append([v.name, v.type, targets])
    return out


def read_drivers(idb):
    """Drivers d'un data-block natif (objet, lumiere, camera) : un addon peut en
    ajouter pendant la session (mise a niveau d'un rig...)."""
    ad = getattr(idb, "animation_data", None)
    if ad is None:
        return []
    out = []
    for fc in ad.drivers:
        try:
            d = fc.driver
            out.append([fc.data_path, fc.array_index, bool(fc.mute), d.type, d.expression,
                        bool(d.use_self), _read_driver_vars(d)])
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _add_driver(idb, path, index):
    try:
        fc = idb.driver_add(path, index)
    except TypeError:
        fc = None
    if fc is None and index == 0:
        try:
            fc = idb.driver_add(path)      # propriete simple (pas un tableau)
        except TypeError:
            fc = None
    if isinstance(fc, (list, tuple)):
        fc = fc[index] if index < len(fc) else None
    return fc


def apply_drivers(idb, want, ctx):
    ad = getattr(idb, "animation_data", None)
    have = {}
    for fc in (ad.drivers if ad is not None else ()):
        have.setdefault((fc.data_path, fc.array_index), fc)
    unresolved = None
    for path, index, mute, dtype, expr, use_self, variables in want:
        fc = have.pop((path, index), None)
        if fc is None:
            fc = _add_driver(idb, path, index)
            if fc is None:
                unresolved = Unresolved("driver " + path)   # os ou modificateur pas encore la
                continue
        d = fc.driver
        if fc.mute != mute:
            fc.mute = mute
        if d.type != dtype:
            d.type = dtype
        if d.expression != expr:
            d.expression = expr
        if d.use_self != use_self:
            d.use_self = use_self
        if _read_driver_vars(d) == variables:
            continue
        while len(d.variables):
            d.variables.remove(d.variables[0])
        for name, vtype, targets in variables:
            var = d.variables.new()
            var.name = name
            var.type = vtype
            for t, td in zip(var.targets, targets):
                if "id_type" in td and vtype == "SINGLE_PROP":
                    try:
                        t.id_type = td["id_type"]
                    except Exception:  # noqa: BLE001
                        pass
                for k in _DRV_TARGET[1:]:
                    if k in td and hasattr(t, k):
                        try:
                            setattr(t, k, td[k])
                        except Exception:  # noqa: BLE001
                            pass
                try:
                    t.id = resolve_ref(td.get("id"), ctx)
                except Unresolved as exc:
                    unresolved = exc
                except Exception:  # noqa: BLE001
                    pass
    if ad is not None:
        for fc in have.values():
            try:
                ad.drivers.remove(fc)
            except Exception:  # noqa: BLE001
                pass
    if unresolved is not None:
        raise unresolved


def apply_anim(idb, v, ctx):
    ad = getattr(idb, "animation_data", None)
    if v is None:
        if ad is not None and ad.action is not None:
            ad.action = None
        return
    act = resolve_ref(v.get("a"), ctx)
    if ad is None:
        ad = idb.animation_data_create()
    if ad.action != act:
        ad.action = act
    slot_id = v.get("s")
    if act is not None and slot_id and compat.has_rna_prop(ad, "action_slot"):
        try:
            cur = ad.action_slot
            if cur is None or cur.identifier != slot_id:
                for s in act.slots:
                    if s.identifier == slot_id:
                        ad.action_slot = s
                        break
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Objets
# ---------------------------------------------------------------------------

OBJ_SKIP = frozenset((
    "matrix_world", "matrix_local", "matrix_basis", "dimensions", "active_material",
    "active_material_index", "active_shape_key_index", "mode", "pose", "motion_path",
    "animation_visualization", "particle_systems", "rigid_body", "rigid_body_constraint",
    "soft_body", "select", "bound_box", "shape_key_lock", "use_dynamic_topology_sculpting",
    "data",
))

MOD_SKIP = frozenset(("name", "type", "show_expanded", "is_active", "is_override_data",
                      "persistent_uid", "execution_time", "use_apply_on_spline"))
CON_SKIP = frozenset(("name", "type", "show_expanded", "is_valid", "active",
                      "is_override_data", "error_location", "error_rotation"))


def read_object(obj, ctx, deep=True):
    f = read_struct(obj, depth=1, skip=OBJ_SKIP)
    f["name"] = obj.name
    f["data"] = ref(obj.data)
    f["_matslots"] = [[s.link, ref(s.material) if s.link == "OBJECT" else None]
                      for s in obj.material_slots]
    mods = []
    for m in obj.modifiers:
        d = {"n": m.name, "t": m.type, "p": read_struct(m, depth=0, skip=MOD_SKIP)}
        if m.type == "NODES":
            d["i"] = read_idprops(m, rna=m)
        if m.type == "PARTICLE_SYSTEM":
            try:
                d["ps"] = ref(m.particle_system.settings)
            except Exception:
                pass
        mods.append(d)
    f["_mods"] = mods
    f["_cons"] = [{"n": c.name, "t": c.type, "p": read_struct(c, depth=0, skip=CON_SKIP)}
                  for c in obj.constraints]
    f["_vg"] = [[g.name, bool(g.lock_weight)] for g in obj.vertex_groups]
    f["_props"] = read_idprops(obj, rna=obj)
    f["_anim"] = read_anim(obj)
    f["_colls"] = _object_collections(obj)
    if obj.type == "ARMATURE" and obj.pose is not None:
        pose = {}
        for pb in obj.pose.bones:
            pose[pb.name] = [
                [_round(x) for x in pb.location], pb.rotation_mode,
                [_round(x) for x in pb.rotation_quaternion],
                [_round(x) for x in pb.rotation_euler],
                [_round(x) for x in pb.rotation_axis_angle],
                [_round(x) for x in pb.scale],
                read_idprops(pb, rna=pb),
            ]
        f["_pose"] = pose
    f["_drv"] = read_drivers(obj)
    return f


# Champs relus seuls quand Blender ne signale qu'un deplacement de l'objet.
FAST_OBJECT_FIELDS = ("location", "rotation_mode", "rotation_euler", "rotation_quaternion",
                      "rotation_axis_angle", "scale", "delta_location", "delta_rotation_euler",
                      "delta_rotation_quaternion", "delta_scale")
FAST_OBJECT_SET = frozenset(FAST_OBJECT_FIELDS)


_FAST_PROPS = []


def _fast_props(obj):
    if not _FAST_PROPS:
        props = obj.bl_rna.properties
        for k in FAST_OBJECT_FIELDS:
            p = props.get(k)
            if p is not None:
                arr = getattr(p, "is_array", None)
                if arr is None:
                    arr = getattr(p, "array_length", 0) > 0
                _FAST_PROPS.append((k, p, p.type, bool(arr)))
    return _FAST_PROPS


def read_object_fast(obj):
    """Transformation seule (meme format que read_object), pour un objet
    qui bouge : bien plus rapide que la lecture complete."""
    out = {}
    for k, p, t, arr in _fast_props(obj):
        try:
            v = getattr(obj, k)
            if arr and t == "FLOAT":
                out[k] = [round(float(x), 6) for x in v]
            elif t == "ENUM":
                out[k] = v
            else:
                out[k] = _conv(v, p)
        except Exception:  # noqa: BLE001
            continue
    return out


def _object_collections(obj):
    """Collections contenant l'objet ("S:<uid>" = collection maitre d'une scene).

    Champ informatif : il n'est jamais diffuse comme modification (les
    collections portent l'appartenance) mais sert a replacer un objet recree
    (annulation d'une suppression, retour dans l'historique).
    """
    out = []
    for c in obj.users_collection:
        u = ids.get_uid(c)
        if u:
            out.append(u)
            continue
        for sc in bpy.data.scenes:
            if sc.collection == c:
                su = ids.get_uid(sc)
                if su:
                    out.append("S:" + su)
                break
    out.sort()
    return out


def _apply_object_collections(obj, v, ctx):
    if len(obj.users_collection):
        return
    unresolved = None
    for u in v:
        if u.startswith("S:"):
            sc = ctx.resolve(u[2:])
            coll = sc.collection if sc is not None else None
        else:
            coll = ctx.resolve(u)
        if coll is None:
            unresolved = Unresolved(u)
            continue
        try:
            coll.objects.link(obj)
        except Exception:
            pass
    if unresolved is not None and not len(obj.users_collection):
        raise unresolved


def _sync_stack(coll, want, new_fn):
    """Aligne une pile (modificateurs / contraintes) sur la liste voulue."""
    cur = [(m.name, m.type) for m in coll]
    target = [(d["n"], d["t"]) for d in want]
    if cur == target:
        return
    tset = set(target)
    for m in list(coll):
        if (m.name, m.type) not in tset:
            coll.remove(m)
    for idx, (n, t) in enumerate(target):
        m = coll.get(n)
        if m is not None and m.type != t:
            coll.remove(m)
            m = None
        if m is None:
            try:
                m = new_fn(n, t)
            except Exception:
                continue
            if m.name != n:
                try:
                    m.name = n
                except Exception:
                    pass
        cur_idx = coll.find(m.name)
        if cur_idx != idx and 0 <= idx < len(coll) and hasattr(coll, "move"):
            try:
                coll.move(cur_idx, idx)
            except Exception:
                pass


def _apply_mods(obj, mods, ctx):
    _sync_stack(obj.modifiers, mods, lambda n, t: obj.modifiers.new(n, t))
    unresolved = None
    for d in mods:
        m = obj.modifiers.get(d["n"])
        if m is None:
            continue
        p = dict(d.get("p", {}))
        # node_group d'abord : le changer reinitialise les entrees
        if "node_group" in p:
            try:
                apply_prop(m, "node_group", p.pop("node_group"), ctx)
            except Unresolved as exc:
                unresolved = exc
        try:
            apply_struct(m, p, ctx)
        except Unresolved as exc:
            unresolved = exc
        if "i" in d:
            try:
                apply_idprops(m, d["i"], ctx, remove_missing=False)
            except Unresolved as exc:
                unresolved = exc
        if "ps" in d and m.type == "PARTICLE_SYSTEM":
            try:
                st = resolve_ref(d["ps"], ctx)
                if st is not None and m.particle_system.settings != st:
                    m.particle_system.settings = st
            except Unresolved as exc:
                unresolved = exc
    obj.update_tag()
    if unresolved is not None:
        raise unresolved


def _new_constraint(obj, n, t):
    c = obj.constraints.new(t)
    c.name = n
    return c


def _apply_cons(obj, cons, ctx):
    _sync_stack(obj.constraints, cons, lambda n, t: _new_constraint(obj, n, t))
    unresolved = None
    for d in cons:
        c = obj.constraints.get(d["n"])
        if c is None:
            continue
        try:
            apply_struct(c, d.get("p", {}), ctx)
        except Unresolved as exc:
            unresolved = exc
    if unresolved is not None:
        raise unresolved


def _apply_vgroups(obj, want):
    groups = obj.vertex_groups
    for i, (name, lock) in enumerate(want):
        if i < len(groups):
            g = groups[i]
            if g.name != name:
                g.name = name
        else:
            g = groups.new(name=name)
        if g.lock_weight != lock:
            g.lock_weight = lock
    while len(groups) > len(want):
        groups.remove(groups[len(groups) - 1])


def _apply_pose(obj, pose, ctx):
    bones = getattr(obj.data, "bones", None)
    if obj.pose is None:
        if bones is not None and len(bones):
            raise Unresolved("pose")  # pose pas encore construite par Blender
        return
    missing = False
    for name, vals in pose.items():
        pb = obj.pose.bones.get(name)
        if pb is None:
            if bones is not None and name in bones:
                missing = True
            continue
        loc, rmode, quat, eul, aa, scale, props = vals
        if pb.rotation_mode != rmode:
            pb.rotation_mode = rmode
        pb.location = loc
        pb.rotation_quaternion = quat
        pb.rotation_euler = eul
        pb.rotation_axis_angle = aa
        pb.scale = scale
        if props:
            apply_idprops(pb, props, ctx, remove_missing=False)
    if missing:
        try:
            obj.data.update_tag()
        except Exception:
            pass
        raise Unresolved("pose")


def _apply_matslots(obj, slots, ctx):
    unresolved = None
    for i, (link, mref) in enumerate(slots):
        if i >= len(obj.material_slots):
            break
        slot = obj.material_slots[i]
        try:
            if slot.link != link:
                slot.link = link
            if link == "OBJECT":
                mat = resolve_ref(mref, ctx)
                if slot.material != mat:
                    slot.material = mat
        except Unresolved as exc:
            unresolved = exc
        except Exception:
            pass
    if unresolved is not None:
        raise unresolved


def apply_object_field(obj, k, v, ctx):
    if k == "name":
        if obj.name != v:
            obj.name = v
    elif k == "data":
        target = resolve_ref(v, ctx)
        if obj.data != target:
            try:
                obj.data = target
            except Exception:
                pass
    elif k == "_matslots":
        _apply_matslots(obj, v, ctx)
    elif k == "_mods":
        _apply_mods(obj, v, ctx)
    elif k == "_cons":
        _apply_cons(obj, v, ctx)
    elif k == "_vg":
        _apply_vgroups(obj, v)
    elif k == "_props":
        apply_idprops(obj, v, ctx)
    elif k == "_anim":
        apply_anim(obj, v, ctx)
    elif k == "_pose":
        _apply_pose(obj, v, ctx)
    elif k == "_drv":
        apply_drivers(obj, v, ctx)
    elif k == "_colls":
        _apply_object_collections(obj, v, ctx)
    else:
        apply_prop(obj, k, v, ctx)


# Champs lus mais jamais diffuses comme modification
PASSIVE_FIELDS = frozenset(("_colls",))


# ---------------------------------------------------------------------------
# Maillages
# ---------------------------------------------------------------------------

ATTR_SPEC = {
    "FLOAT": ("value", 1, np.float32), "INT": ("value", 1, np.int32),
    "INT8": ("value", 1, np.int32), "BOOLEAN": ("value", 1, np.bool_),
    "FLOAT_VECTOR": ("vector", 3, np.float32), "FLOAT2": ("vector", 2, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32), "BYTE_COLOR": ("color", 4, np.float32),
    "INT32_2D": ("value", 2, np.int32), "QUATERNION": ("value", 4, np.float32),
    "FLOAT4X4": ("value", 16, np.float32), "INT16_2D": ("value", 2, np.int32),
}


def _attr_name(obj, attr):
    try:
        x = getattr(obj, attr)
        return x.name if x is not None else None
    except Exception:
        return None


def geometry_bytes(me):
    """Maillage -> octets, avec tous ses attributs (y compris en mode Edition).

    En mode Edition, me.attributes decrit le BMesh : ses tableaux sont vides, et
    sharp_face, sharp_edge ou material_index n'y existent pas. Sans eux, l'autre
    joueur verrait un maillage tout lisse, sans UV ni couleurs. obj.to_mesh() donne
    une copie hors de bpy.data, sans BMesh, de l'etat ecrit par update_from_editmode.
    """
    if me.is_editmode:
        owner = next((o for o in bpy.data.objects if o.data == me and o.mode == "EDIT"), None)
        if owner is not None:
            try:
                return _geometry_bytes(owner.to_mesh())
            finally:
                owner.to_mesh_clear()
    return _geometry_bytes(me)


def _geometry_bytes(me):
    nv, ne, nl, npoly = len(me.vertices), len(me.edges), len(me.loops), len(me.polygons)
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    ed = np.empty(ne * 2, np.int32)
    me.edges.foreach_get("vertices", ed)
    lv = np.empty(nl, np.int32)
    me.loops.foreach_get("vertex_index", lv)
    ps = np.empty(npoly, np.int32)
    me.polygons.foreach_get("loop_start", ps)
    arrays = {"co": co, "edges": ed, "loop_v": lv, "poly_start": ps}
    sizes = {"POINT": nv, "EDGE": ne, "FACE": npoly, "CORNER": nl}
    attrs = []
    for a in me.attributes:
        n = a.name
        if n.startswith(".") or n == "position":
            continue
        spec = ATTR_SPEC.get(a.data_type)
        cnt = sizes.get(a.domain)
        if spec is None or cnt is None:
            continue
        key, size, dt = spec
        try:
            if len(a.data) != cnt:
                continue
            buf = np.empty(cnt * size, dt)
            a.data.foreach_get(key, buf)
        except Exception:
            continue
        arrays["a:" + n] = buf
        attrs.append([n, a.domain, a.data_type])
    meta = {"nv": nv, "ne": ne, "nl": nl, "np": npoly, "attrs": attrs,
            "uv": _attr_name(me.uv_layers, "active")}
    try:
        meta["col"] = me.attributes.active_color_name
        meta["colr"] = me.attributes.default_color_name
    except Exception:
        pass
    try:
        for uv in me.uv_layers:
            if uv.active_render:
                meta["uvr"] = uv.name
    except Exception:
        pass
    return payloads.pack(meta, arrays)


def _loop_edges(nv, ed, lv, ps, nl):
    """Index d'arete de chaque coin, en respectant l'ordre des aretes recues."""
    if nl == 0:
        return np.empty(0, np.int32)
    sizes = np.diff(np.append(ps, nl))
    nxt = np.arange(1, nl + 1, dtype=np.int64)
    ends = ps.astype(np.int64) + sizes - 1
    nxt[ends] = ps
    v1 = lv.astype(np.int64)
    v2 = lv[nxt].astype(np.int64)
    key = np.minimum(v1, v2) * nv + np.maximum(v1, v2)
    e = ed.reshape(-1, 2).astype(np.int64)
    ek = np.minimum(e[:, 0], e[:, 1]) * nv + np.maximum(e[:, 0], e[:, 1])
    order = np.argsort(ek, kind="stable")
    sk = ek[order]
    pos = np.searchsorted(sk, key)
    pos_c = np.clip(pos, 0, max(len(sk) - 1, 0))
    if len(sk) == 0 or not np.all(sk[pos_c] == key):
        return None
    return order[pos_c].astype(np.int32)


def apply_geometry(me, data):
    if me.is_editmode:
        raise Locked(me.name)
    meta, arrays, _raw = payloads.unpack(data)
    nv, ne, nl, npoly = meta["nv"], meta["ne"], meta["nl"], meta["np"]
    co = arrays["co"]
    ed = arrays["edges"]
    lv = arrays["loop_v"]
    ps = arrays["poly_start"]
    me.clear_geometry()
    me.vertices.add(nv)
    me.vertices.foreach_set("co", co)
    me.edges.add(ne)
    if ne:
        me.edges.foreach_set("vertices", ed)
    me.loops.add(nl)
    me.polygons.add(npoly)
    edges_ok = True
    if nl:
        me.loops.foreach_set("vertex_index", lv)
        me.polygons.foreach_set("loop_start", ps)
        le = _loop_edges(nv, ed, lv, ps, nl)
        if le is None:
            edges_ok = False
        else:
            me.loops.foreach_set("edge_index", le)
    if not edges_ok:
        me.update(calc_edges=True)
    want = set()
    for n, dom, dtype in meta.get("attrs", []):
        if dom == "EDGE" and not edges_ok:
            continue
        want.add(n)
        arr = arrays.get("a:" + n)
        spec = ATTR_SPEC.get(dtype)
        if arr is None or spec is None:
            continue
        a = me.attributes.get(n)
        if a is not None and (a.domain != dom or a.data_type != dtype):
            try:
                me.attributes.remove(a)
            except Exception:
                pass
            a = None
        if a is None:
            try:
                a = me.attributes.new(n, dtype, dom)
            except Exception:
                continue
        try:
            a = me.attributes.get(n)
            a.data.foreach_set(spec[0], arr)
        except Exception:
            pass
    for a in list(me.attributes):
        n = a.name
        if n.startswith(".") or n == "position" or n in want:
            continue
        if getattr(a, "is_required", False) or getattr(a, "is_internal", False):
            continue
        try:
            me.attributes.remove(me.attributes[n])
        except Exception:
            pass
    uv = meta.get("uv")
    if uv and uv in me.uv_layers:
        try:
            me.uv_layers.active = me.uv_layers[uv]
        except Exception:
            pass
    uvr = meta.get("uvr")
    if uvr and uvr in me.uv_layers:
        try:
            me.uv_layers[uvr].active_render = True
        except Exception:
            pass
    for key, attr in (("col", "active_color_name"), ("colr", "default_color_name")):
        if meta.get(key):
            try:
                setattr(me.attributes, attr, meta[key])
            except Exception:
                pass
    me.update()


def _mesh_extra_hash(me):
    """Empreinte des cles de forme et poids de groupes (mode blob)."""
    import hashlib
    h = hashlib.sha1()
    sk = me.shape_keys
    if sk is not None:
        h.update(("%s%s" % (sk.use_relative, sk.reference_key.name if sk.reference_key else "")).encode())
        # valeurs pilotees (drivers, animation) : recalculees chez chacun, hors empreinte
        driven = compat.animated_paths(sk.animation_data) if sk.animation_data else {}
        for kb in sk.key_blocks:
            path = 'key_blocks["%s"].value' % kb.name.replace("\\", "\\\\").replace('"', '\\"')
            value = "pilote" if path in driven else "%.5f" % kb.value
            h.update(("%s|%s|%d|%.4f|%.4f|%s|%s|%s;" % (
                kb.name, value, kb.mute, kb.slider_min, kb.slider_max,
                kb.relative_key.name if kb.relative_key else "", kb.vertex_group,
                kb.interpolation)).encode())
            buf = np.empty(len(kb.data) * 3, np.float32)
            kb.data.foreach_get("co", buf)
            h.update(np.round(buf, 5).tobytes())
    nv = len(me.vertices)
    if nv <= 300000:
        parts = []
        for v in me.vertices:
            for g in v.groups:
                parts.append((v.index, g.group, round(g.weight, 4)))
        h.update(repr(parts).encode())
    return h.hexdigest()


def mesh_needs_blob(me, ctx):
    if me.shape_keys is not None:
        return True
    return ctx.mesh_has_vgroups(me)


def read_mesh(me, ctx, deep=True):
    f = {"name": me.name, "_mats": [ref(m) for m in me.materials],
         "_props": read_idprops(me, rna=me)}
    if deep:
        geom = geometry_bytes(me)
        gsha = payloads.sha(geom)
        if mesh_needs_blob(me, ctx):
            f["_mode"] = "b"
            f["fp"] = payloads.sha((gsha + _mesh_extra_hash(me)).encode())
        else:
            f["_mode"] = "n"
            ctx.store.put(geom, key=gsha)
            f["geom"] = {"$p": gsha}
    return f


def _apply_mesh_mats(me, mats, ctx):
    want = []
    unresolved = None
    for r in mats:
        try:
            want.append(resolve_ref(r, ctx))
        except Unresolved as exc:
            unresolved = exc
            want.append(None)
    if list(me.materials) != want:
        # Ne pas utiliser clear() : il remet a zero les index de materiau.
        while len(me.materials) > len(want):
            me.materials.pop()
        for i, m in enumerate(want):
            if i < len(me.materials):
                if me.materials[i] != m:
                    me.materials[i] = m
            else:
                me.materials.append(m)
    if unresolved is not None:
        raise unresolved


def apply_mesh_field(me, k, v, ctx):
    if k == "name":
        if me.name != v:
            me.name = v
    elif k == "_mats":
        _apply_mesh_mats(me, v, ctx)
    elif k == "_props":
        apply_idprops(me, v, ctx)
    elif k == "geom":
        data = ctx.store.get(v["$p"])
        if data is None:
            raise MissingPayload(v["$p"])
        if me.shape_keys is not None:
            for obj in ctx.users_of(me):
                try:
                    obj.shape_key_clear()
                    break
                except Exception:
                    continue
        apply_geometry(me, data)
    # "_mode" et "fp" sont informatifs ; "blob" est traite par apply_blob.


# ---------------------------------------------------------------------------
# Types "natifs" simples : lumieres, cameras
# ---------------------------------------------------------------------------

def read_generic_id(idb, ctx, deep=True):
    f = read_struct(idb, depth=1)
    f["name"] = idb.name
    f["_props"] = read_idprops(idb, rna=idb)
    f["_anim"] = read_anim(idb)
    f["_drv"] = read_drivers(idb)
    nt = getattr(idb, "node_tree", None)
    if deep and nt is not None and isinstance(idb, bpy.types.Light):
        # Noeuds de la lumiere (Cycles : IES, corps noir...) : envoyes en bloc
        # complet quand ils changent.
        try:
            f["_mode"] = "b"
            f["fp"] = fingerprint.fingerprint(nt)
        except Exception:  # noqa: BLE001
            f.pop("_mode", None)
    return f


def apply_generic_field(idb, k, v, ctx):
    if k in ("_mode", "fp"):
        return
    if k == "name":
        if idb.name != v:
            idb.name = v
    elif k == "_props":
        apply_idprops(idb, v, ctx)
    elif k == "_anim":
        apply_anim(idb, v, ctx)
    elif k == "_drv":
        apply_drivers(idb, v, ctx)
    else:
        apply_prop(idb, k, v, ctx)


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def _uids(seq):
    out = []
    for x in seq:
        u = ids.get_uid(x)
        if u:
            out.append(u)
    out.sort()
    return out


def read_collection(coll, ctx, deep=True):
    f = read_struct(coll, depth=0)
    f["name"] = coll.name
    f["_children"] = _uids(coll.children)
    f["_objects"] = _uids(coll.objects)
    f["_props"] = read_idprops(coll, rna=coll)
    return f


def _sync_links(link_coll, want_uids, ctx):
    unresolved = None
    want = {}
    for u in want_uids:
        idb = ctx.resolve(u)
        if idb is None:
            unresolved = Unresolved(u)
            continue
        want[idb.as_pointer()] = idb
    cur = {x.as_pointer(): x for x in link_coll}
    for ptr, idb in want.items():
        if ptr not in cur:
            try:
                link_coll.link(idb)
            except Exception:
                pass
    for ptr, idb in cur.items():
        # On ne retire que des data-blocks suivis (les autres sont locaux).
        if ptr not in want and ids.get_uid(idb):
            try:
                link_coll.unlink(idb)
            except Exception:
                pass
    if unresolved is not None:
        raise unresolved


def apply_collection_field(coll, k, v, ctx):
    if k == "name":
        if coll.name != v:
            coll.name = v
    elif k == "_children":
        _sync_links(coll.children, v, ctx)
    elif k == "_objects":
        _sync_links(coll.objects, v, ctx)
    elif k == "_props":
        apply_idprops(coll, v, ctx)
    else:
        apply_prop(coll, k, v, ctx)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

SCENE_SKIP = frozenset((
    "frame_current", "frame_float", "frame_current_final", "cursor", "tool_settings",
    "sequence_editor", "keying_sets", "keying_sets_all", "active_clip", "show_subframe",
    "use_audio_scrub", "sync_mode", "use_preview_range", "frame_preview_start",
    "frame_preview_end", "is_nla_tweakmode", "statistics", "rigidbody_world",
    "safe_areas", "display", "shading", "grease_pencil_settings", "hydra",
))

# Reglages de rendu PARTAGES : moteur, reglages Cycles / EEVEE / Workbench, sortie,
# unites, gestion des couleurs : tout le monde voit la meme image que l'hote.
# Seuls restent propres a chaque PC le peripherique (GPU/CPU), les performances
# et la qualite de la vue 3D (LOCAL_RENDER_KEYS).
_SCENE_SUBS = (
    ("_unit", "unit_settings", 0, ()),
    ("_view", "view_settings", 0, ()),
    ("_display", "display_settings", 0, ()),
)

OUTPUT_PREFIXES = ("resolution_", "pixel_aspect", "fps", "frame_map", "filepath", "use_file_extension",
                   "use_overwrite", "use_placeholder", "image_settings", "ffmpeg", "border_",
                   "use_border", "use_crop_to_border", "stamp", "use_stamp", "metadata_input",
                   "use_multiview", "views_format", "use_single_layer")

# Reglages de rendu restaures chez le joueur apres reception de la scene de l'hote.
# Sous-reglages de rendu partages : (cle du message, attribut de la scene)
RENDER_SUBS = (("_rnd", "render"), ("_cyc", "cycles"), ("_eev", "eevee"), ("_wb", "display"))
# Propres a chaque PC : materiel, performances, qualite de la vue 3D.
LOCAL_RENDER_KEYS = {
    "render": frozenset(("threads_mode", "threads", "use_persistent_data", "preview_pixel_size",
                         "use_lock_interface", "compositor_device", "compositor_precision")),
    "cycles": frozenset(("device", "denoising_use_gpu", "preview_denoising_use_gpu", "preview_samples",
                         "use_auto_tile", "tile_size", "shading_system",
                         "texture_limit", "texture_limit_render", "debug_use_spatial_splits",
                         "debug_use_hair_bvh", "debug_bvh_type", "debug_use_compact_bvh",
                         "debug_bvh_time_steps")),
    "eevee": frozenset(("taa_samples", "use_taa_reprojection", "shadow_pool_size",
                        "use_shadow_jitter_viewport", "gi_irradiance_pool_size")),
    "display": frozenset(),
}
_LOCAL_PREFIXES = ("debug_",)


def _is_local_render_key(attr, k):
    return k in LOCAL_RENDER_KEYS.get(attr, ()) or (attr == "cycles" and k.startswith(_LOCAL_PREFIXES))


def _set_engine(render, v):
    """Change le moteur. EEVEE s'appelle BLENDER_EEVEE_NEXT en 4.2-4.x et
    BLENDER_EEVEE ailleurs : on essaie l'autre nom si besoin."""
    names = [v]
    if v in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        names += [n for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if n != v]
    for name in names:
        if render.engine == name:
            return
        try:
            render.engine = name
            return
        except (TypeError, ValueError):
            continue


def read_render_settings(scene):
    """Reglages de rendu partages, par sous-ensemble ({cle: {prop: valeur}})."""
    out = {}
    for key, attr in RENDER_SUBS:
        sub = getattr(scene, attr, None)
        if sub is None:
            continue
        try:
            if attr == "render":
                full = read_struct(sub, depth=1, skip=("views", "stereo_views"))
                d = {k: v for k, v in full.items() if not _is_output_key(k)}
            else:
                d = read_struct(sub, depth=1)
        except Exception:  # noqa: BLE001
            continue
        out[key] = {k: v for k, v in d.items() if not _is_local_render_key(attr, k)}
    return out


def apply_render_settings(scene, key, v, ctx):
    attr = dict(RENDER_SUBS).get(key)
    sub = getattr(scene, attr, None) if attr else None
    if sub is None or not isinstance(v, dict):
        return
    d = {k: x for k, x in v.items() if not _is_local_render_key(attr, k)}
    if attr == "render" and "engine" in d:
        _set_engine(sub, d.pop("engine"))   # d'abord le moteur : certains reglages en dependent
    apply_struct(sub, d, ctx)


def _is_output_key(k):
    return k.startswith(OUTPUT_PREFIXES)


def read_output(scene):
    full = read_struct(scene.render, depth=1, skip=("views", "stereo_views"))
    return {k: v for k, v in full.items() if _is_output_key(k)}


def capture_local_render(scene):
    """Reglages propres a ce PC (peripherique, performances, qualite de la vue 3D),
    pour les garder en rejoignant une session."""
    out = {}
    for _key, attr in RENDER_SUBS:
        sub = getattr(scene, attr, None)
        if sub is None:
            continue
        d = {}
        for p in sub.bl_rna.properties:
            k = p.identifier
            if _is_local_render_key(attr, k) and not p.is_readonly and p.type != "POINTER":
                try:
                    d[k] = _conv(getattr(sub, k), p)
                except Exception:  # noqa: BLE001
                    continue
        if d:
            out[attr] = d
    return out


def restore_local_render(scene, saved, ctx):
    for attr, values in (saved or {}).items():
        sub = scene.render if attr == "render" else getattr(scene, attr, None)
        if sub is None:
            continue
        try:
            apply_struct(sub, values, ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.log("reglages de rendu locaux (%s): %s" % (attr, exc))


def _read_layers(scene):
    out = {}
    for vl in scene.view_layers:
        d = {}
        stack = [vl.layer_collection]
        while stack:
            lc = stack.pop()
            for ch in lc.children:
                u = ids.get_uid(ch.collection)
                if u:
                    d[u] = [bool(ch.exclude), bool(ch.hide_viewport), bool(ch.holdout),
                            bool(ch.indirect_only)]
                stack.append(ch)
        out[vl.name] = d
    return out


def _apply_layers(scene, v):
    for vl in scene.view_layers:
        d = v.get(vl.name)
        if not d:
            continue
        stack = [vl.layer_collection]
        while stack:
            lc = stack.pop()
            for ch in lc.children:
                vals = d.get(ids.get_uid(ch.collection))
                if vals:
                    for attr, val in zip(("exclude", "hide_viewport", "holdout", "indirect_only"), vals):
                        try:
                            if getattr(ch, attr) != val:
                                setattr(ch, attr, val)
                        except Exception:
                            pass
                stack.append(ch)


def _read_hidden(scene):
    out = {}
    for vl in scene.view_layers:
        hidden = []
        for o in vl.objects:
            try:
                if o.hide_get(view_layer=vl):
                    u = ids.get_uid(o)
                    if u:
                        hidden.append(u)
            except Exception:
                pass
        hidden.sort()
        out[vl.name] = hidden
    return out


def _apply_hidden(scene, v, ctx):
    for vl in scene.view_layers:
        want = set(v.get(vl.name, []))
        for o in vl.objects:
            u = ids.get_uid(o)
            if not u:
                continue
            h = u in want
            try:
                if o.hide_get(view_layer=vl) != h:
                    o.hide_set(h, view_layer=vl)
            except Exception:
                pass


def read_scene(scene, ctx, deep=True):
    skip = SCENE_SKIP if not ctx.sync_frame else (SCENE_SKIP - {"frame_current"})
    f = read_struct(scene, depth=0, skip=skip)
    f["name"] = scene.name
    for key, attr, depth, sub_skip in _SCENE_SUBS:
        sub = getattr(scene, attr, None)
        if sub is not None:
            try:
                f[key] = read_struct(sub, depth=depth, skip=sub_skip)
            except Exception:
                pass
    try:
        f["_output"] = read_output(scene)
    except Exception:  # noqa: BLE001
        pass
    f.update(read_render_settings(scene))
    f["_children"] = _uids(scene.collection.children)
    f["_objects"] = _uids(scene.collection.objects)
    f["_layers"] = _read_layers(scene)
    f["_props"] = read_idprops(scene, rna=scene)
    if deep:
        f["_hidden"] = _read_hidden(scene)
    return f


def apply_scene_field(scene, k, v, ctx):
    if k == "name":
        if scene.name != v:
            scene.name = v
        return
    if k == "_children":
        _sync_links(scene.collection.children, v, ctx)
        return
    if k == "_objects":
        _sync_links(scene.collection.objects, v, ctx)
        return
    if k == "_layers":
        _apply_layers(scene, v)
        return
    if k == "_hidden":
        _apply_hidden(scene, v, ctx)
        return
    if k == "_props":
        apply_idprops(scene, v, ctx)
        return
    if k == "_output":
        apply_struct(scene.render, {kk: vv for kk, vv in v.items() if _is_output_key(kk)}, ctx)
        return
    if k in ("_rnd", "_cyc", "_eev", "_wb"):
        apply_render_settings(scene, k, v, ctx)
        return
    if k in ("_render", "_cycles", "_eevee"):
        return  # anciens champs (versions < 1.2)
    for key, attr, _depth, _skip in _SCENE_SUBS:
        if k == key:
            sub = getattr(scene, attr, None)
            if sub is not None:
                apply_struct(sub, v, ctx)
            return
    if k == "frame_current" and not ctx.sync_frame:
        return
    apply_prop(scene, k, v, ctx)


# ---------------------------------------------------------------------------
# Blobs : data-block complet via un mini fichier .blend
# ---------------------------------------------------------------------------

MAX_ATTACHED_FILE = 96 * 1024 * 1024


FILE_ATTRS = ("images", "fonts", "sounds", "movieclips", "volumes", "cache_files")


def external_files(idb):
    """Fichiers externes utilises par un data-block : [(chemin, numero de tuile)]."""
    if getattr(idb, "packed_file", None) is not None or getattr(idb, "library", None) is not None:
        return []
    fp = getattr(idb, "filepath", "") or ""
    if not fp:
        return []
    try:
        path = bpy.path.abspath(fp)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(idb, bpy.types.Image) and idb.source == "TILED" and "<UDIM>" in path:
        out = []
        for tile in idb.tiles:
            p = path.replace("<UDIM>", str(tile.number))
            if os.path.isfile(p):
                out.append((p, tile.number))
        return out
    if isinstance(idb, bpy.types.Image) and idb.source not in ("FILE", "MOVIE"):
        return []
    return [(path, None)] if os.path.isfile(path) else []


def file_signature(idb):
    sig = []
    for path, tile in external_files(idb):
        try:
            st = os.stat(path)
            sig.append([tile, int(st.st_mtime * 1000), st.st_size])
        except OSError:
            pass
    return sig or None


def read_blob_id(idb, ctx, deep=True):
    f = {"name": idb.name}
    if deep:
        f["fp"] = fingerprint.fingerprint(idb)
        if isinstance(idb, (bpy.types.Image, bpy.types.VectorFont, bpy.types.Sound,
                            bpy.types.MovieClip)) or type(idb).__name__ in ("Volume", "CacheFile"):
            f["fsig"] = file_signature(idb)
    return f


MAX_PIXELS = 4096 * 4096


def pixels_supported(img):
    try:
        w, h = img.size
    except Exception:  # noqa: BLE001
        return False
    # Les images creees dans Blender (celles que l'on peint) sont de type UV_TEST.
    return (img.type in ("IMAGE", "UV_TEST") and img.source in ("FILE", "GENERATED") and w * h > 0
            and w * h <= MAX_PIXELS and img.library is None)


def image_pixels(img):
    """(octets, meta) des pixels d'une image, en 8 bits (ou 16 bits flottants pour le HDR)."""
    w, h = img.size
    ch = img.channels
    arr = np.empty(w * h * ch, dtype=np.float32)
    img.pixels.foreach_get(arr)
    if img.is_float:
        return arr.astype(np.float16).tobytes(), {"w": w, "h": h, "c": ch, "fmt": "f16"}
    data = (np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes()
    return data, {"w": w, "h": h, "c": ch, "fmt": "u8"}


def apply_pixels(img, v, ctx):
    if not v:
        return
    key = v["px"]["$p"]
    data = ctx.store.get(key)
    if data is None:
        raise MissingPayload(key)
    w, h, ch = int(v["w"]), int(v["h"]), int(v["c"])
    if tuple(img.size) != (w, h):
        img.scale(w, h)
    if img.channels != ch:
        ctx.log("image %s : canaux differents (%d/%d), pixels ignores" % (img.name, img.channels, ch))
        return
    if v.get("fmt") == "f16":
        arr = np.frombuffer(data, dtype=np.float16).astype(np.float32)
    else:
        arr = np.frombuffer(data, dtype=np.uint8).astype(np.float32) * (1.0 / 255.0)
    img.pixels.foreach_set(arr)
    img.update()


def write_blob(idb, attr, ctx):
    """Ecrit le data-block (et ses dependances) et retourne les champs a envoyer."""
    path = os.path.join(compat.temp_dir(), "out_%s.blend" % (ids.get_uid(idb) or "x"))
    compat.libraries_write(path, {idb})
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        os.remove(path)
    except OSError:
        pass
    meta = {"name": idb.name, "attr": attr}
    fields = {}
    if attr in FILE_ATTRS:
        files = external_files(idb)
        if files and files[0][1] is not None:
            # Image UDIM : une piece jointe par tuile.
            tiles = []
            for path, number in files:
                try:
                    if os.path.getsize(path) <= MAX_ATTACHED_FILE:
                        with open(path, "rb") as fh:
                            tiles.append({"$p": ctx.store.put(fh.read())})
                        meta.setdefault("tiles", []).append([number, os.path.basename(path)])
                except OSError:
                    pass
            if tiles:
                fields["tiles"] = tiles
                meta["pattern"] = os.path.basename(bpy.path.abspath(idb.filepath))
        elif files:
            abspath = files[0][0]
            try:
                if os.path.getsize(abspath) <= MAX_ATTACHED_FILE:
                    with open(abspath, "rb") as fh:
                        fdata = fh.read()
                    fields["file"] = {"$p": ctx.store.put(fdata)}
                    meta["fname"] = os.path.basename(abspath)
            except OSError:
                pass
    data = payloads.pack(meta, raw=raw)
    key = ctx.store.put(data)
    fields["blob"] = {"$p": key}
    return fields


def _snapshot_pointers():
    snap = set()
    for attr in ids.active_types():
        for idb in getattr(bpy.data, attr):
            snap.add(idb.as_pointer())
    return snap


def apply_blob(attr, uid, fields, existing, ctx):
    """Importe un data-block depuis un blob et remplace l'existant."""
    bref = fields.get("blob")
    data = ctx.store.get(bref["$p"]) if bref else None
    if data is None:
        raise MissingPayload(bref["$p"] if bref else "?")
    if existing is not None and getattr(existing, "is_editmode", False):
        raise Locked(existing.name)
    # Chemin d'origine du fichier externe : garde si le contenu recu est identique
    # (evite de remplacer les chemins de l'hote par des copies).
    orig_path = None
    if existing is not None and attr in FILE_ATTRS:
        files = external_files(existing)
        if len(files) == 1 and files[0][1] is None:
            orig_path = (existing.filepath, files[0][0])
    meta, _arrays, raw = payloads.unpack(data)
    blob_name = meta["name"]
    blob_attr = meta.get("attr", attr)
    path = os.path.join(compat.temp_dir(), "in_%s.blend" % uid)
    with open(path, "wb") as fh:
        fh.write(raw)
    before = _snapshot_pointers()
    libs_before = {lib.as_pointer() for lib in bpy.data.libraries}
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        if blob_name not in getattr(src, blob_attr):
            raise ValueError("blob sans data-block %s" % blob_name)
        setattr(dst, blob_attr, [blob_name])
    loaded = getattr(dst, blob_attr)
    target = loaded[0] if loaded else None
    if target is None:
        raise ValueError("import impossible de %s" % blob_name)
    # Dependances importees : on reutilise les data-blocks deja presents.
    for a in ids.active_types():
        for nid in list(getattr(bpy.data, a)):
            if nid.as_pointer() in before or nid == target:
                continue
            nu = ids.get_uid(nid)
            if not nu:
                continue
            if nu in ctx.tombstones:
                try:
                    ids.remove_id(a, nid)
                except Exception:
                    pass
                continue
            e = ctx.resolve(nu)
            if e is not None and e != nid:
                try:
                    nid.user_remap(e)
                except Exception:
                    pass
                ids.remove_id(a, nid)
            else:
                ctx.adopt(nu, nid, a)
    if existing is not None and existing != target:
        try:
            existing.user_remap(target)
        except Exception:
            pass
        ids.remove_id(attr, existing)
    ids.set_uid(target, uid)
    name = fields.get("name") or blob_name
    if target.name != name:
        target.name = name
    # Fichier externe joint (image, police, son...)
    fref = fields.get("file")
    if fref and hasattr(target, "filepath"):
        fdata = ctx.store.get(fref["$p"])
        if fdata is not None:
            keep = None
            if orig_path is not None:
                try:
                    with open(orig_path[1], "rb") as fh:
                        if payloads.sha(fh.read()) == fref["$p"]:
                            keep = orig_path[0]
                except OSError:
                    pass
            if keep is None:
                fname = meta.get("fname") or (target.name + ".bin")
                local = os.path.join(ctx.assets_dir(), "%s_%s" % (fref["$p"][:10], fname))
                if not os.path.exists(local):
                    with open(local, "wb") as fh:
                        fh.write(fdata)
                keep = local
            try:
                if target.filepath != keep:
                    target.filepath = keep
                if hasattr(target, "reload"):
                    target.reload()
            except Exception:
                pass
    if fields.get("tiles") and meta.get("tiles") and hasattr(target, "filepath"):
        folder = os.path.join(ctx.assets_dir(), "udim_%s" % uid)
        os.makedirs(folder, exist_ok=True)
        for ref_, (number, fname) in zip(fields["tiles"], meta["tiles"]):
            tdata = ctx.store.get(ref_["$p"])
            if tdata is not None:
                with open(os.path.join(folder, fname), "wb") as fh:
                    fh.write(tdata)
        try:
            target.filepath = os.path.join(folder, meta.get("pattern") or "tile.<UDIM>.png")
            target.reload()
        except Exception:
            pass
    for lib in list(bpy.data.libraries):
        if lib.as_pointer() not in libs_before:
            try:
                bpy.data.libraries.remove(lib)
            except Exception:
                pass
    try:
        os.remove(path)
    except OSError:
        pass
    ctx.register(uid, target, attr)
    return target


# ---------------------------------------------------------------------------
# Repartition par type
# ---------------------------------------------------------------------------

def read(attr, idb, ctx, deep=True):
    mode = ids.mode_of(attr)
    if attr == "objects":
        return read_object(idb, ctx, deep)
    if attr == "meshes":
        return read_mesh(idb, ctx, deep)
    if attr == "collections":
        return read_collection(idb, ctx, deep)
    if attr == "scenes":
        return read_scene(idb, ctx, deep)
    if mode == "native":
        return read_generic_id(idb, ctx, deep)
    return read_blob_id(idb, ctx, deep)


def is_deep_field(attr, k):
    return k in ("fp", "geom", "blob", "_mode", "_hidden", "file", "fsig", "tiles")


def apply_field(attr, idb, k, v, ctx):
    if attr == "objects":
        apply_object_field(idb, k, v, ctx)
    elif attr == "meshes":
        apply_mesh_field(idb, k, v, ctx)
    elif attr == "collections":
        apply_collection_field(idb, k, v, ctx)
    elif attr == "scenes":
        apply_scene_field(idb, k, v, ctx)
    elif ids.mode_of(attr) == "native":
        apply_generic_field(idb, k, v, ctx)
    else:
        if k == "name" and idb.name != v:
            idb.name = v
        elif k == "_px" and isinstance(idb, bpy.types.Image):
            apply_pixels(idb, v, ctx)
        # "fsig" (signature du fichier) est informatif


def apply_fields(attr, uid, idb, fields, ctx):
    """Applique les champs ; retourne le data-block (il peut avoir ete remplace)."""
    if "blob" in fields:
        idb = apply_blob(attr, uid, fields, idb, ctx)
        rest = {k: v for k, v in fields.items() if k not in ("blob", "fp", "file", "name", "tiles")}
    else:
        rest = fields
    order = sorted(rest.keys(), key=lambda k: (0 if k == "name" else 1 if k == "data" else 2))
    for k in order:
        v = rest[k]
        try:
            apply_field(attr, idb, k, v, ctx)
        except Unresolved:
            ctx.defer(uid, attr, k, v)
        except (Locked, MissingPayload):
            raise
        except Exception as exc:  # noqa: BLE001
            ctx.log("champ %s.%s: %s" % (attr, k, exc))
    try:
        idb.update_tag()
    except Exception:
        pass
    return idb


def create(attr, uid, fields, ctx):
    name = fields.get("name") or "Partage"
    if "blob" in fields:
        return apply_fields(attr, uid, None, fields, ctx)
    if attr == "objects":
        data = resolve_ref(fields.get("data"), ctx)  # peut lever Unresolved
        idb = bpy.data.objects.new(name, data)
    elif attr == "meshes":
        idb = bpy.data.meshes.new(name)
    elif attr == "lights":
        idb = bpy.data.lights.new(name, type=fields.get("type", "POINT"))
    elif attr == "cameras":
        idb = bpy.data.cameras.new(name)
    elif attr == "collections":
        idb = bpy.data.collections.new(name)
    elif attr == "scenes":
        idb = bpy.data.scenes.new(name)
    else:
        raise ValueError("creation impossible pour %s sans blob" % attr)
    ids.set_uid(idb, uid)
    ctx.register(uid, idb, attr)
    return apply_fields(attr, uid, idb, fields, ctx)


def delete(attr, idb, ctx):
    if attr == "scenes":
        others = [s for s in bpy.data.scenes if s != idb]
        if not others:
            return False
        for w in compat.windows():
            if w.scene == idb:
                w.scene = others[0]
    return ids.remove_id(attr, idb)
