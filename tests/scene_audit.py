"""Etat de rendu d'une scene : materiaux, images, lumieres, monde (pour comparer hote / invite)."""
import bpy


def audit():
    out = {"mats": {}, "imgs": {}, "lights": {}, "world": None}
    for m in bpy.data.materials:
        if m.users == 0:
            continue
        nt = m.node_tree
        d = {"use_nodes": m.use_nodes}
        if nt:
            d["nodes"] = len(nt.nodes)
            d["links"] = sorted("%s.%s>%s.%s" % (l.from_node.name, l.from_socket.identifier, l.to_node.name,
                                                 l.to_socket.identifier) for l in nt.links)
            d["imgs"] = sorted(n.image.name for n in nt.nodes if getattr(n, "image", None))
        out["mats"][m.name] = d
    for i in bpy.data.images:
        out["imgs"][i.name] = {"src": i.source, "packed": bool(i.packed_file), "has_data": i.has_data,
                               "size": list(i.size), "cs": i.colorspace_settings.name,
                               "alpha": i.alpha_mode, "file": i.filepath[-40:]}
        try:
            out["imgs"][i.name]["px0"] = round(sum(i.pixels[:4000]) / 4000.0, 4) if i.size[0] else None
        except Exception:
            out["imgs"][i.name]["px0"] = "err"
    for o in bpy.context.scene.objects:
        if o.type == "LIGHT":
            out["lights"][o.name] = [o.data.type, round(o.data.energy, 3), o.hide_render, o.hide_viewport,
                                     o.visible_get()]
    w = bpy.context.scene.world
    if w:
        out["world"] = [w.name, w.use_nodes, len(w.node_tree.nodes) if w.node_tree else 0]
    return out


def diff(a, b, path=""):
    res = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            res += diff(a.get(k), b.get(k), path + "/" + str(k))
    elif a != b:
        res.append((path, a, b))
    return res
