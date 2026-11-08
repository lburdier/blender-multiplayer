"""Interface : onglet "Multijoueur" de la barre laterale de la vue 3D (N)."""

import datetime
import os
import time

import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty

from . import net, prefs
from . import session as sess

_MODE_LABELS = {"OBJECT": "Objet", "EDIT": "Edition", "EDIT_MESH": "Edition", "SCULPT": "Sculpt",
                "POSE": "Pose", "WEIGHT_PAINT": "Poids", "VERTEX_PAINT": "Peinture",
                "TEXTURE_PAINT": "Texture"}


class MP_Props(bpy.types.PropertyGroup):
    session_name: StringProperty(name="Nom", default="Ma session")
    address: StringProperty(name="Adresse", default="127.0.0.1",
                            description="Adresse IP de l'hote (LAN, VPN type Tailscale/ZeroTier...)")
    port: IntProperty(name="Port", default=5555, min=1024, max=65535)
    password: StringProperty(name="Mot de passe", subtype="PASSWORD", default="")
    as_viewer: BoolProperty(name="Spectateur", default=False,
                            description="Rejoindre en lecture seule")
    sync_frame: BoolProperty(name="Synchroniser la frame", default=False)
    chat_text: StringProperty(name="", default="")
    checkpoint_label: StringProperty(name="", default="")
    history_page: IntProperty(name="Page", default=0, min=0)
    history_mine: BoolProperty(name="Mes actions", default=False)


class MP_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Session"

    def draw(self, context):
        layout = self.layout
        mp = context.window_manager.mp
        p = prefs.get()
        s = sess.current
        if s is None:
            box = layout.box()
            row = box.row()
            if hasattr(p, "bl_rna"):
                row.prop(p, "username", text="Pseudo")
                row.prop(p, "color", text="")
            else:
                row.label(text="Pseudo : %s" % p.username)
            box = layout.box()
            box.label(text="Heberger", icon="NETWORK_DRIVE")
            box.prop(mp, "session_name")
            row = box.row(align=True)
            row.prop(mp, "port")
            box.prop(mp, "password")
            box.prop(mp, "sync_frame")
            box.operator("mp.host", icon="PLAY")
            box = layout.box()
            box.label(text="Rejoindre", icon="URL")
            box.prop(mp, "address")
            box.prop(mp, "port")
            box.prop(mp, "password")
            box.prop(mp, "as_viewer")
            box.operator("mp.join", icon="LINKED")
            last = getattr(sess, "last_reason", "")
            if last:
                layout.label(text=last, icon="INFO")
            return
        box = layout.box()
        if s.is_host:
            box.label(text="Hote : %s" % s.session_name, icon="NETWORK_DRIVE")
            for ip in net.local_addresses()[:3]:
                row = box.row(align=True)
                addr = "%s:%d" % (ip, s.port)
                row.label(text=addr, icon="WORLD")
                op = row.operator("mp.copy_address", text="", icon="COPYDOWN")
                op.address = addr
            row = box.row()
            op = row.operator("mp.sync_frame", text="Frame partagee" if s.sync_frame else
                              "Frame individuelle", icon="TIME", depress=s.sync_frame)
            op.value = not s.sync_frame
        else:
            if s.state == "ready":
                box.label(text="Connecte a %s" % (s.session_name or s.address), icon="LINKED")
                box.label(text="%s:%d - ping %d ms" % (s.address, s.port, s.rtt))
                if s.role == "viewer":
                    box.label(text="Mode spectateur (lecture seule)", icon="HIDE_OFF")
                if s.host_ver and s.host_ver != bpy.app.version_string.split(" ")[0]:
                    box.label(text="Hote sous Blender %s" % s.host_ver, icon="ERROR")
            elif s.state == "sync":
                pct = int(s.conn.rx_progress * 100) if s.conn else 0
                box.label(text="Reception de la scene... %d%%" % pct, icon="IMPORT")
            else:
                box.label(text="Connexion a %s:%d..." % (s.address, s.port), icon="TIME")
        row = layout.row(align=True)
        row.operator("mp.undo", text="Annuler", icon="LOOP_BACK")
        row.operator("mp.redo", text="Retablir", icon="LOOP_FORWARDS")
        row = layout.row(align=True)
        row.operator("mp.mark", icon="CURSOR")
        if not s.is_host:
            row.operator("mp.resync", text="", icon="FILE_REFRESH")
        row.operator("mp.open_folder", text="", icon="FILE_FOLDER")
        layout.operator("mp.leave", icon="CANCEL")


class MP_PT_users(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Joueurs"
    bl_parent_id = "MP_PT_main"

    @classmethod
    def poll(cls, context):
        return sess.current is not None and sess.current.active

    def draw(self, context):
        layout = self.layout
        s = sess.current
        for uid, u in sorted(s.users.items()):
            box = layout.box()
            pres = u.get("presence") or {}
            row = box.row(align=True)
            me = uid == s.me
            role = {"host": "hote", "viewer": "spectateur"}.get(u.get("role"), "")
            title = u.get("name", "?") + (" (vous)" if me else "") + (" - " + role if role else "")
            row.label(text=title, icon="USER" if not me else "HOME")
            if not me:
                op = row.operator("mp.follow", text="", icon="HIDE_OFF",
                                  depress=(s.follow == uid))
                op.user = uid
                op = row.operator("mp.goto", text="", icon="VIEW_CAMERA")
                op.user = uid
                if s.is_host:
                    op = row.operator("mp.role", text="", icon="LOCKED" if u.get("role") == "viewer"
                                      else "UNLOCKED")
                    op.user = uid
                    op = row.operator("mp.kick", text="", icon="X")
                    op.user = uid
            col = box.column(align=True)
            col.scale_y = 0.75
            mode = _MODE_LABELS.get(pres.get("mode", "OBJECT"), pres.get("mode", ""))
            act = pres.get("actn") or "-"
            col.label(text="%s - %s - frame %s" % (mode, act, pres.get("frame", "?")))
            extra = []
            if pres.get("sel"):
                extra.append("%d selectionne(s)" % len(pres["sel"]))
            if not me and pres.get("rtt"):
                extra.append("ping %d ms" % pres["rtt"])
            if u.get("ver"):
                extra.append("Blender %s" % u["ver"])
            if extra:
                col.label(text=" - ".join(extra))


class MP_PT_history(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Historique"
    bl_parent_id = "MP_PT_main"

    @classmethod
    def poll(cls, context):
        return sess.current is not None and sess.current.active

    def draw(self, context):
        layout = self.layout
        s = sess.current
        mp = context.window_manager.mp
        items = s.history_items()[::-1]
        if mp.history_mine:
            items = [h for h in items if h.get("u") == s.me]
        row = layout.row()
        row.prop(mp, "history_mine", toggle=True, icon="USER")
        row.label(text="%d action(s)" % len(items))
        per_page = 12
        pages = max(1, (len(items) + per_page - 1) // per_page)
        page = min(mp.history_page, pages - 1)
        col = layout.column(align=True)
        for h in items[page * per_page:(page + 1) * per_page]:
            row = col.row(align=True)
            when = datetime.datetime.fromtimestamp(h["t"]).strftime("%H:%M")
            kind = h.get("k")
            icon = {"undo": "LOOP_BACK", "redo": "LOOP_FORWARDS", "revert": "RECOVER_LAST",
                    "checkpoint": "FILE_TICK", "restore": "FILE_BACKUP"}.get(kind, "DOT")
            sub = row.row(align=True)
            sub.active = not h.get("x")
            who = "moi" if h.get("u") == s.me else (h.get("n") or "?").split(" ")[0][:10]
            sub.label(text="%s [%s] %s" % (when, who, h.get("l", "")), icon=icon)
            if h.get("nc"):
                op = row.operator("mp.history_undo", text="", icon="X")
                op.seq = h["s"]
            op = row.operator("mp.history_revert", text="", icon="RECOVER_LAST")
            op.seq = h["s"]
        if pages > 1:
            row = layout.row(align=True)
            row.prop(mp, "history_page", text="Page")
            row.label(text="/ %d" % pages)


class MP_PT_backups(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Sauvegardes"
    bl_parent_id = "MP_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return sess.current is not None and sess.current.active

    def draw(self, context):
        layout = self.layout
        s = sess.current
        p = prefs.get()
        if hasattr(p, "bl_rna"):
            row = layout.row()
            row.prop(p, "autosave_minutes", text="Auto (min)")
            row.prop(p, "max_backups", text="Garder")
        if not s.is_host:
            layout.label(text="Copies locales : %s" % ("oui" if p.local_backup else "non"),
                         icon="FILE_BACKUP")
            layout.label(text="Les points de restauration sont geres par l'hote.")
            return
        row = layout.row(align=True)
        row.prop(context.window_manager.mp, "checkpoint_label", text="")
        row.operator("mp.checkpoint", text="Sauvegarder", icon="FILE_TICK")
        col = layout.column(align=True)
        for mtime, kind, path in s.backups()[:15]:
            row = col.row(align=True)
            when = datetime.datetime.fromtimestamp(mtime).strftime("%d/%m %H:%M:%S")
            icon = {"checkpoints": "FILE_TICK", "autosave": "TIME"}.get(kind, "FILE_BACKUP")
            row.label(text="%s  %s" % (when, os.path.basename(path)[:28]), icon=icon)
            op = row.operator("mp.restore", text="", icon="FILE_REFRESH")
            op.filepath = path


class MP_PT_chat(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Chat"
    bl_parent_id = "MP_PT_main"

    @classmethod
    def poll(cls, context):
        return sess.current is not None and sess.current.active

    def draw(self, context):
        layout = self.layout
        s = sess.current
        col = layout.column(align=True)
        col.scale_y = 0.8
        for item in list(s.chat)[-10:]:
            if item.get("n"):
                col.label(text="%s : %s" % (item["n"], item["text"]))
            else:
                col.label(text=item["text"], icon="INFO")
        row = layout.row(align=True)
        row.prop(context.window_manager.mp, "chat_text", text="")
        row.operator("mp.chat", text="", icon="PLAY")


class MP_PT_display(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Multijoueur"
    bl_label = "Affichage"
    bl_parent_id = "MP_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = prefs.get()
        if not hasattr(p, "bl_rna"):
            self.layout.label(text="Preferences indisponibles")
            return
        col = self.layout.column(align=True)
        for name in ("show_avatars", "show_names", "show_selection", "show_cursors",
                     "show_toasts", "override_undo"):
            col.prop(p, name)


def draw_header(self, context):
    s = sess.current
    if s is None:
        return
    layout = self.layout
    if s.active:
        layout.label(text="%d en ligne" % len(s.users), icon="COMMUNITY")
    else:
        layout.label(text="Connexion...", icon="TIME")


classes = (MP_Props, MP_PT_main, MP_PT_users, MP_PT_history, MP_PT_chat, MP_PT_backups,
           MP_PT_display)


def register():
    bpy.types.WindowManager.mp = PointerProperty(type=MP_Props)
    bpy.types.VIEW3D_HT_header.append(draw_header)


def unregister():
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_header)
    except Exception:
        pass
    try:
        del bpy.types.WindowManager.mp
    except Exception:
        pass
