"""Diagnostic rendu (hote)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp_common as T  # noqa: E402
import render_util as R  # noqa: E402

import bpy  # noqa: E402

T.ROLE = "host"
session = T.session


def save_png(path, rgba, size=8):
    img = bpy.data.images.new("tmp_save", size, size)
    img.pixels.foreach_set(list(rgba) * (size * size))
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


try:
    texdir = os.path.join(T.OUT, "tex")
    os.makedirs(texdir, exist_ok=True)
    p = os.path.join(texdir, "rouge.png")
    save_png(p, (1, 0.2, 0.1, 1))
    tex = bpy.data.images.load(p)
    mat = bpy.data.materials.new("MatTex")
    mat.use_nodes = True
    n = mat.node_tree.nodes.new("ShaderNodeTexImage")
    n.image = tex
    mat.node_tree.links.new(n.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    cube = bpy.data.objects["Cube"]
    cube.data.materials.append(mat)
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -1))
    w = bpy.context.scene.world
    if w is None:
        w = bpy.data.worlds.new("World")
        bpy.context.scene.world = w
    T.pump(0.3)
    base = {e: R.render_mean(T.OUT, "host0", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU hote initial", base)
    s = session.host(T.PORT, "", "Diag", username="Hote")
    T.wait_for(lambda: len(s.users) == 2, 60, "client")
    T.barrier("C0", 120)
    # L'hote ajoute une lumiere forte
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 5))
    bpy.context.active_object.data.energy = 8.0
    bpy.context.active_object.name = "SoleilHote"
    T.pump(1.5)
    T.say("H1")
    T.barrier("C1", 120)
    T.pump(1.0)
    after = {e: R.render_mean(T.OUT, "host2", e) for e in ("CYCLES", "EEVEE")}
    T.log("RENDU hote apres lumiere client", after)
    T.RESULTS["renders"] = {"base": base, "after": after}
    T.say("END")
    T.pump(1.0)
except Exception:
    import traceback
    T.RESULTS["errors"].append(traceback.format_exc())
    traceback.print_exc()
T.finish()
if T.session.current is not None:
    T.session.current.stop()
