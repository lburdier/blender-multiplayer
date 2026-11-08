"""Empreinte (hash) generique d'un data-block par parcours de son RNA.

Sert a detecter qu'un materiau, un arbre de nodes, une animation, une
armature... a change, sans dependre de l'API specifique de chaque version :
le parcours s'adapte automatiquement aux proprietes exposees par Blender
(actions "layered" de 5.x, interface des node groups, Grease Pencil v3...).

Seules les proprietes modifiables comptent (les proprietes en lecture seule
sont des valeurs derivees qui changent toutes seules, ex: taille d'image
chargee). Les etats d'interface (selection, panneaux deplies) sont ignores.
"""

import hashlib

import bpy
import numpy as np

from . import ids

MAX_DEPTH = 12
MAX_COLLECTION = 20000
MAX_VALUES = 400000

_SKIP = frozenset((
    "rna_type", "bl_rna", "name_full", "original", "users", "is_evaluated",
    "is_embedded_data", "is_missing", "is_runtime_data", "tag", "library",
    "library_weak_reference", "override_library", "preview", "asset_data",
    "session_uid", "id_type", "is_library_indirect", "use_extra_user", "is_editmode",
    "select", "select_head", "select_tail", "select_control_point",
    "select_left_handle", "select_right_handle", "show_expanded", "is_active",
    "pixels", "bindcode", "is_dirty", "has_data", "internal_links", "edit_bones",
    "is_updated", "view_center", "show_options", "show_preview", "dimensions",
    "is_active_output", "is_selected", "use_fake_user", "frame_current",
))

# Proprietes a ignorer pour un type de struct precis
_STRUCT_SKIP = {
    "GreasePencilDrawing": frozenset(("strokes",)),
    # file_format, resolution : les lire oblige Blender a decoder toute l'image (tres lent).
    "Image": frozenset(("render_slots", "pixels", "file_format", "resolution")),
    "Mesh": frozenset(("vertices", "edges", "loops", "polygons", "loop_triangles",
                       "loop_triangle_polygons", "polygon_normals", "vertex_normals",
                       "corner_normals", "uv_layers", "color_attributes")),
}

# Proprietes en lecture seule qui portent pourtant l'information utile
_RO_INCLUDE = {
    "Bone": frozenset(("head_local", "tail_local", "matrix_local", "length")),
}

# Collections d'elements tres nombreux lues d'un bloc avec foreach_get
_BULK = {
    "Keyframe": (("co", 2), ("handle_left", 2), ("handle_right", 2)),
    "BezierSplinePoint": (("co", 3), ("handle_left", 3), ("handle_right", 3), ("radius", 1),
                          ("tilt", 1)),
    "SplinePoint": (("co", 4), ("radius", 1), ("tilt", 1)),
    "LatticePoint": (("co_deform", 3),),
    "ShapeKeyPoint": (("co", 3),),
    "MeshVertex": (("co", 3),),
}

_ATTR_KEYS = {
    "FLOAT": ("value", 1, np.float32), "INT": ("value", 1, np.int32),
    "INT8": ("value", 1, np.int32), "BOOLEAN": ("value", 1, np.bool_),
    "FLOAT_VECTOR": ("vector", 3, np.float32), "FLOAT2": ("vector", 2, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32), "BYTE_COLOR": ("color", 4, np.float32),
    "INT32_2D": ("value", 2, np.int32), "QUATERNION": ("value", 4, np.float32),
    "FLOAT4X4": ("value", 16, np.float32), "INT16_2D": ("value", 2, np.int32),
}

_id_struct_cache = {}


def is_id_struct(srna):
    if srna is None:
        return False
    key = srna.identifier
    res = _id_struct_cache.get(key)
    if res is None:
        res = False
        s = srna
        while s is not None:
            if s.identifier == "ID":
                res = True
                break
            s = s.base
        _id_struct_cache[key] = res
    return res


def _fmt(v):
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, (bool, int)):
        return str(int(v))
    if isinstance(v, str):
        return v
    if isinstance(v, set):
        return ",".join(sorted(v))
    try:
        return "(" + ",".join(_fmt(x) for x in v) + ")"
    except TypeError:
        return str(v)


class _Walker:
    def __init__(self):
        self.h = hashlib.sha1()
        self.seen = set()
        self.values = 0

    def w(self, s):
        self.h.update(s.encode("utf-8", "replace"))

    def token(self, s):
        try:
            return s.path_from_id()
        except Exception:
            return s.bl_rna.identifier

    def bulk(self, coll, item_ident, n):
        spec = _BULK.get(item_ident)
        if spec is None:
            return False
        try:
            for key, size in spec:
                buf = np.empty(n * size, dtype=np.float32)
                coll.foreach_get(key, buf)
                self.h.update(np.round(buf, 5).tobytes())
        except Exception:
            return False
        if item_ident == "Keyframe" and n <= 4000:
            try:
                self.w("".join(k.interpolation[0] + k.handle_left_type[0] for k in coll))
            except Exception:
                pass
        self.values += n
        return True

    def attribute(self, attr):
        spec = _ATTR_KEYS.get(getattr(attr, "data_type", None))
        self.w("A:%s:%s:%s;" % (attr.name, attr.domain, attr.data_type))
        if spec is None:
            return
        key, size, dt = spec
        try:
            n = len(attr.data)
            buf = np.empty(n * size, dtype=dt)
            attr.data.foreach_get(key, buf)
            if buf.dtype.kind == "f":
                buf = np.round(buf, 5)
            self.h.update(buf.tobytes())
            self.values += n
        except Exception:
            pass

    def walk(self, s, depth):
        if s is None or self.values > MAX_VALUES:
            return
        try:
            ptr = s.as_pointer()
        except Exception:
            ptr = id(s)
        if ptr in self.seen:
            self.w("@" + self.token(s) + ";")
            return
        self.seen.add(ptr)
        srna = s.bl_rna
        ident = srna.identifier
        if isinstance(s, bpy.types.Text):
            try:
                self.w(s.as_string())
            except Exception:
                pass
            return
        if isinstance(s, bpy.types.Attribute):
            self.attribute(s)
            return
        skip = _STRUCT_SKIP.get(ident, ())
        ro_inc = _RO_INCLUDE.get(ident, ())
        for p in srna.properties:
            k = p.identifier
            if k in _SKIP or k in skip:
                continue
            t = p.type
            if t == "POINTER":
                try:
                    v = getattr(s, k)
                except Exception:
                    continue
                if v is None:
                    self.w(k + "=N;")
                    continue
                if is_id_struct(p.fixed_type):
                    if getattr(v, "is_embedded_data", False) and depth > 0:
                        self.w(k + "{")
                        self.walk(v, depth - 1)
                        self.w("}")
                    else:
                        self.w("%s=#%s;" % (k, ids.get_uid(v) or getattr(v, "name", "?")))
                elif depth > 0:
                    self.w(k + "{")
                    self.walk(v, depth - 1)
                    self.w("}")
            elif t == "COLLECTION":
                if depth <= 0:
                    continue
                try:
                    c = getattr(s, k)
                    n = len(c)
                except Exception:
                    continue
                self.w("%s[%d]" % (k, n))
                if n == 0 or n > MAX_COLLECTION:
                    continue
                item_ident = p.fixed_type.identifier if p.fixed_type else ""
                if self.bulk(c, item_ident, n):
                    continue
                for item in c:
                    if item is None:
                        self.w("N;")
                    elif isinstance(item, bpy.types.ID):
                        self.w("#%s;" % (ids.get_uid(item) or item.name))
                    else:
                        self.walk(item, depth - 1)
            else:
                if p.is_readonly and k not in ro_inc:
                    continue
                try:
                    v = getattr(s, k)
                except Exception:
                    continue
                self.values += 1
                self.w(k + "=" + _fmt(v) + ";")


def fingerprint(idb):
    w = _Walker()
    w.seen.add(idb.as_pointer())
    srna = idb.bl_rna
    skip = _STRUCT_SKIP.get(srna.identifier, ())
    # Le nom de l'ID est synchronise a part : on le laisse hors de l'empreinte.
    w.w(srna.identifier + ";")
    if isinstance(idb, bpy.types.Text):
        w.w(idb.as_string())
        return w.h.hexdigest()
    for p in srna.properties:
        k = p.identifier
        if k in _SKIP or k in skip or k == "name":
            continue
        t = p.type
        if t not in ("POINTER", "COLLECTION") and p.is_readonly:
            # Ne pas lire : certaines (Image.depth...) obligent a decoder toute l'image.
            continue
        try:
            v = getattr(idb, k)
        except Exception:
            continue
        if t == "POINTER":
            if v is None:
                w.w(k + "=N;")
            elif is_id_struct(p.fixed_type):
                if getattr(v, "is_embedded_data", False):
                    w.w(k + "{")
                    w.walk(v, MAX_DEPTH)
                    w.w("}")
                else:
                    w.w("%s=#%s;" % (k, ids.get_uid(v) or getattr(v, "name", "?")))
            else:
                w.w(k + "{")
                w.walk(v, MAX_DEPTH)
                w.w("}")
        elif t == "COLLECTION":
            try:
                n = len(v)
            except Exception:
                continue
            w.w("%s[%d]" % (k, n))
            if n == 0 or n > MAX_COLLECTION:
                continue
            item_ident = p.fixed_type.identifier if p.fixed_type else ""
            if w.bulk(v, item_ident, n):
                continue
            for item in v:
                if item is None:
                    w.w("N;")
                elif isinstance(item, bpy.types.ID):
                    w.w("#%s;" % (ids.get_uid(item) or item.name))
                else:
                    w.walk(item, MAX_DEPTH)
        else:
            if p.is_readonly:
                continue
            w.w(k + "=" + _fmt(v) + ";")
    # Proprietes personnalisees (hors UID)
    try:
        for key in sorted(idb.keys()):
            if key.startswith("_"):
                continue
            val = idb[key]
            if hasattr(val, "to_list"):
                val = val.to_list()
            elif hasattr(val, "to_dict"):
                continue
            elif isinstance(val, bpy.types.ID):
                val = ids.get_uid(val) or val.name
            w.w("P:%s=%s;" % (key, _fmt(val)))
    except Exception:
        pass
    return w.h.hexdigest()
