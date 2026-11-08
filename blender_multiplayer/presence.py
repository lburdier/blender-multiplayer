"""Presence des joueurs dans la scene.

- capture locale : point de vue 3D, curseur, selection, objet actif, mode ;
- dessin des autres joueurs dans toutes les vues 3D : "camera" coloree a
  leur position, nom, ligne de visee, boites autour de leur selection,
  curseur 3D, objets verrouilles, pings, messages du chat ;
- suivi : coller sa vue a celle d'un autre joueur.
"""

import math
import time

import bpy
from mathutils import Matrix, Vector

from . import compat, ids

_handles = []


def _mat(flat):
    return Matrix([flat[0:4], flat[4:8], flat[8:12], flat[12:16]])


def capture(session):
    """Etat de presence local (dict JSON) ou None sans fenetre."""
    win, area, region, space = compat.largest_view3d()
    d = {}
    if space is not None:
        r3d = space.region_3d
        try:
            vm = r3d.view_matrix.inverted()
            d["vm"] = [round(x, 4) for row in vm for x in row]
            d["persp"] = r3d.view_perspective
            d["lens"] = round(space.lens, 2)
            d["dist"] = round(r3d.view_distance, 4)
            d["loc"] = [round(x, 4) for x in r3d.view_location]
            d["rot"] = [round(x, 5) for x in r3d.view_rotation]
        except Exception:
            pass
    scene = compat.current_scene()
    vl = compat.current_view_layer()
    if scene is not None:
        d["cur"] = [round(x, 4) for x in scene.cursor.location]
        d["frame"] = scene.frame_current
        d["scene"] = ids.get_uid(scene)
    if vl is not None:
        act = vl.objects.active
        d["act"] = ids.get_uid(act) if act else None
        d["actn"] = act.name if act else ""
        d["mode"] = act.mode if act else "OBJECT"
        sel = []
        edit = []
        for o in session.tracker.objects_in_mode(time.time()):
            u = ids.get_uid(o)
            if u:
                edit.append(u)
                if o.data is not None:
                    du = ids.get_uid(o.data)
                    if du:
                        edit.append(du)
        try:
            for o in vl.objects:
                if o.select_get(view_layer=vl):
                    u = ids.get_uid(o)
                    if u:
                        sel.append(u)
                        if len(sel) >= 256:
                            break
        except Exception:
            pass
        d["sel"] = sel
        d["edit"] = edit
    return d


# ---------------------------------------------------------------------------
# Suivi d'un joueur
# ---------------------------------------------------------------------------

def apply_follow(pres, state):
    """Copie la vue d'un joueur dans la plus grande vue 3D locale."""
    _w, area, _region, space = compat.largest_view3d()
    if space is None or not pres or "loc" not in pres:
        return False
    r3d = space.region_3d
    # Si l'utilisateur a bouge la vue lui-meme, on arrete de suivre.
    last = state.get("last")
    if last is not None:
        cur = (tuple(round(x, 3) for x in r3d.view_location),
               tuple(round(x, 3) for x in r3d.view_rotation), round(r3d.view_distance, 3))
        if cur != last:
            return False
    try:
        r3d.view_location = pres["loc"]
        r3d.view_rotation = pres["rot"]
        r3d.view_distance = pres["dist"]
        if pres.get("persp") in ("PERSP", "ORTHO"):
            r3d.view_perspective = pres["persp"]
        elif pres.get("persp") == "CAMERA":
            r3d.view_perspective = "PERSP"
        space.lens = pres.get("lens", space.lens)
    except Exception:
        return False
    state["last"] = (tuple(round(x, 3) for x in r3d.view_location),
                     tuple(round(x, 3) for x in r3d.view_rotation), round(r3d.view_distance, 3))
    area.tag_redraw()
    return True


def goto(pres):
    _w, area, _region, space = compat.largest_view3d()
    if space is None or not pres or "loc" not in pres:
        return False
    r3d = space.region_3d
    r3d.view_location = pres["loc"]
    r3d.view_rotation = pres["rot"]
    r3d.view_distance = pres["dist"]
    if pres.get("persp") in ("PERSP", "ORTHO"):
        r3d.view_perspective = pres["persp"]
    area.tag_redraw()
    return True


# ---------------------------------------------------------------------------
# Dessin
# ---------------------------------------------------------------------------

def _session():
    from . import session as _s
    return _s.current


def _prefs():
    from . import prefs
    return prefs.get()


def _color(user, alpha=1.0):
    c = user.get("color", (1, 0.5, 0.1))
    return (c[0], c[1], c[2], alpha)


_BOX_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
              (0, 4), (1, 5), (2, 6), (3, 7))


def _frustum_lines(vm, scale, lens, ortho):
    origin = vm.translation
    half_w = 0.5 * scale
    half_h = half_w * 9.0 / 16.0
    depth = scale * max(0.3, lens / 36.0)
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        if ortho:
            pts.append(vm @ Vector((sx * half_w, sy * half_h, -depth)))
        else:
            pts.append(vm @ Vector((sx * half_w, sy * half_h, -depth)))
    lines = []
    base = [vm @ Vector((sx * half_w * 0.35, sy * half_h * 0.35, 0.0))
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))] if ortho else [origin] * 4
    for i in range(4):
        lines += [base[i], pts[i]]
        lines += [pts[i], pts[(i + 1) % 4]]
    # petit triangle "haut"
    top_l = vm @ Vector((-half_w * 0.4, half_h * 1.1, -depth))
    top_r = vm @ Vector((half_w * 0.4, half_h * 1.1, -depth))
    top = vm @ Vector((0.0, half_h * 1.6, -depth))
    lines += [top_l, top, top, top_r, top_r, top_l]
    # ligne de visee
    lines += [origin, vm @ Vector((0.0, 0.0, -depth * 6.0))]
    return lines


def _box_lines(obj):
    try:
        mw = obj.matrix_world
        corners = [mw @ Vector(c) for c in obj.bound_box]
    except Exception:
        return []
    out = []
    for a, b in _BOX_EDGES:
        out += [corners[a], corners[b]]
    return out


def _cross(p, size):
    p = Vector(p)
    return [p - Vector((size, 0, 0)), p + Vector((size, 0, 0)),
            p - Vector((0, size, 0)), p + Vector((0, size, 0)),
            p - Vector((0, 0, size)), p + Vector((0, 0, size))]


def _circle(p, r, n=24):
    p = Vector(p)
    out = []
    for i in range(n):
        a0 = 2 * math.pi * i / n
        a1 = 2 * math.pi * (i + 1) / n
        out += [p + Vector((math.cos(a0) * r, math.sin(a0) * r, 0)),
                p + Vector((math.cos(a1) * r, math.sin(a1) * r, 0))]
    return out


def _draw_lines(coords, color, width=2.0):
    if not coords:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    res = compat.line_shader()
    if res is None:
        return
    shader, thick = res
    batch = batch_for_shader(shader, "LINES", {"pos": [tuple(c) for c in coords]})
    shader.bind()
    if thick:
        region = bpy.context.region
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", width)
    else:
        try:
            gpu.state.line_width_set(width)
        except Exception:
            pass
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_view():
    s = _session()
    if s is None or not s.active:
        return
    p = _prefs()
    import gpu
    rv3d = bpy.context.region_data
    if rv3d is None:
        return
    viewer = rv3d.view_matrix.inverted().translation
    gpu.state.blend_set("ALPHA")
    now = time.time()
    try:
        for uid, user in s.users.items():
            if uid == s.me:
                continue
            pres = user.get("presence") or {}
            col = _color(user)
            if p.show_avatars and "vm" in pres and s.follow != uid:
                vm = _mat(pres["vm"])
                dist = (vm.translation - viewer).length
                scale = max(0.25, min(4.0, dist * 0.06))
                _draw_lines(_frustum_lines(vm, scale, pres.get("lens", 50),
                                           pres.get("persp") == "ORTHO"), col, 2.5)
            if p.show_selection:
                coords = []
                for ou in pres.get("sel", [])[:64]:
                    obj = s.tracker.index.get(ou)
                    if isinstance(obj, bpy.types.Object):
                        coords += _box_lines(obj)
                _draw_lines(coords, _color(user, 0.85), 1.5)
            if p.show_cursors and "cur" in pres:
                _draw_lines(_cross(pres["cur"], 0.25), _color(user, 0.9), 2.0)
        # objets verrouilles (en cours d'edition par quelqu'un)
        coords_by_user = {}
        for ou, owner in s.locks.items():
            if owner == s.me:
                continue
            obj = s.tracker.index.get(ou)
            if isinstance(obj, bpy.types.Object):
                coords_by_user.setdefault(owner, []).extend(_box_lines(obj))
        for owner, coords in coords_by_user.items():
            user = s.users.get(owner)
            if user:
                _draw_lines(coords, _color(user, 0.5), 4.0)
        for ping in list(s.pings):
            age = now - ping["t"]
            if age > 6:
                continue
            user = s.users.get(ping["u"], {})
            r = 0.3 + (age % 1.5) * 0.8
            _draw_lines(_circle(ping["loc"], r), _color(user, max(0.0, 1 - age / 6)), 3.0)
            _draw_lines(_cross(ping["loc"], 0.15), _color(user, 1.0), 3.0)
    finally:
        gpu.state.blend_set("NONE")


def draw_pixel():
    s = _session()
    if s is None:
        return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    p = _prefs()
    region = bpy.context.region
    rv3d = bpy.context.region_data
    font = 0
    ui = bpy.context.preferences.view.ui_scale
    now = time.time()
    if rv3d is not None and s.active:
        compat.blf_size(font, 13 * ui)
        for uid, user in s.users.items():
            if uid == s.me:
                continue
            pres = user.get("presence") or {}
            col = _color(user)
            if p.show_names and "vm" in pres and s.follow != uid:
                pos = _mat(pres["vm"]).translation
                xy = location_3d_to_region_2d(region, rv3d, pos)
                if xy is not None:
                    blf.color(font, *col)
                    mode = pres.get("mode", "OBJECT")
                    txt = user.get("name", "?")
                    if mode != "OBJECT":
                        txt += "  [%s]" % _MODE_LABELS.get(mode, mode)
                    blf.position(font, xy.x + 8, xy.y + 8, 0)
                    blf.draw(font, txt)
            if p.show_names and pres.get("act"):
                obj = s.tracker.index.get(pres["act"])
                if isinstance(obj, bpy.types.Object) and uid != s.me:
                    try:
                        xy = location_3d_to_region_2d(region, rv3d, obj.matrix_world.translation)
                    except Exception:
                        xy = None
                    if xy is not None:
                        blf.color(font, col[0], col[1], col[2], 0.9)
                        blf.position(font, xy.x + 6, xy.y - 16, 0)
                        blf.draw(font, "< " + user.get("name", "?"))
        for ou, owner in s.locks.items():
            if owner == s.me:
                continue
            obj = s.tracker.index.get(ou)
            user = s.users.get(owner)
            if isinstance(obj, bpy.types.Object) and user:
                try:
                    xy = location_3d_to_region_2d(region, rv3d, obj.matrix_world.translation)
                except Exception:
                    xy = None
                if xy is not None:
                    blf.color(font, *_color(user))
                    blf.position(font, xy.x - 20, xy.y + 14, 0)
                    blf.draw(font, "Verrouille : %s" % user.get("name", "?"))
        for ping in list(s.pings):
            if now - ping["t"] > 6:
                continue
            xy = location_3d_to_region_2d(region, rv3d, Vector(ping["loc"]))
            user = s.users.get(ping["u"], {})
            if xy is not None:
                blf.color(font, *_color(user))
                blf.position(font, xy.x + 10, xy.y + 10, 0)
                blf.draw(font, "! %s" % user.get("name", "?"))
    # Bandeau d'etat + messages recents
    if p.show_toasts:
        compat.blf_size(font, 12 * ui)
        y = 20 * ui
        x = 20 * ui
        for item in list(s.toasts)[-6:][::-1]:
            age = now - item["t"]
            if age > 9:
                continue
            alpha = 1.0 if age < 7 else max(0.0, 1 - (age - 7) / 2)
            c = item.get("c", (1, 1, 1))
            blf.color(font, c[0], c[1], c[2], alpha)
            blf.position(font, x, y, 0)
            blf.draw(font, item["text"])
            y += 18 * ui
        status = s.status_line()
        if status:
            compat.blf_size(font, 11 * ui)
            blf.color(font, 0.9, 0.9, 0.9, 0.75)
            blf.position(font, x, region.height - 60 * ui, 0)
            blf.draw(font, status)


_MODE_LABELS = {"EDIT": "Edition", "EDIT_MESH": "Edition", "SCULPT": "Sculpt",
                "POSE": "Pose", "WEIGHT_PAINT": "Poids", "VERTEX_PAINT": "Peinture",
                "TEXTURE_PAINT": "Texture", "PAINT_GREASE_PENCIL": "Dessin"}


def register():
    if bpy.app.background:
        return
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_view, (), "WINDOW", "POST_VIEW"))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_pixel, (), "WINDOW", "POST_PIXEL"))


def unregister():
    while _handles:
        h = _handles.pop()
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, "WINDOW")
        except Exception:
            pass
