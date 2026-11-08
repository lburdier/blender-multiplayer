"""Rendu d'une petite image et luminosite moyenne (tests de rendu)."""
import os

import bpy


def eevee_name():
    items = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    for n in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if n in items:
            return n
    return items[0]


def render_mean(out_dir, tag, engine="CYCLES"):
    sc = bpy.context.scene
    r = sc.render
    keep = (r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath,
            r.image_settings.file_format)
    try:
        r.engine = engine if engine == "CYCLES" else eevee_name()
        if engine == "CYCLES":
            sc.cycles.samples = 8
            sc.cycles.device = "CPU"
        r.resolution_x, r.resolution_y, r.resolution_percentage = 96, 54, 100
        r.image_settings.file_format = "PNG"
        path = os.path.join(out_dir, "%s_%s.png" % (tag, engine.lower()))
        r.filepath = path
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(path, check_existing=False)
        px = list(img.pixels)
        n = len(px) // 4
        mean = sum(px[i * 4] + px[i * 4 + 1] + px[i * 4 + 2] for i in range(n)) / (3.0 * n)
        bpy.data.images.remove(img)
        return round(mean, 4)
    finally:
        (r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath,
         r.image_settings.file_format) = keep
