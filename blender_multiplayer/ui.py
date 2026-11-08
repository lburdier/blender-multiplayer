"""Interface : onglet "Multijoueur" de la barre laterale de la vue 3D (N)."""

import datetime
import os
import time

import bpy
from bpy.props import BoolProperty, IntProperty, PointerProperty, StringProperty

from . import invite, net, prefs, updater
from . import session as sess

_MODE_LABELS = {"OBJECT": "Objet", "EDIT": "Edition", "EDIT_MESH": "Edition", "SCULPT": "Sculpt",
                "POSE": "Pose", "WEIGHT_PAINT": "Poids", "VERTEX_PAINT": "Peinture",
                "TEXTURE_PAINT": "Texture"}


class MP_Props(bpy.types.PropertyGroup):
    session_name: StringProperty(name="Nom", default="Ma session")
    address: StringProperty(name="Adresse", default="",
                            description="Adresse donnee par l'hote : IP du reseau local "
                                        "(192.168.x.x), adresse Internet (bore.pub:41234) ou lien "
                                        "d'invitation")
    online: BoolProperty(name="Accessible par Internet", default=True,
                         description="Rend la session joignable depuis n'importe quel reseau via un "
                                     "relais, sans VPN ni ouverture de port, et donne un lien "
                                     "d'invitation prive (mot de passe genere si vide)")
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
            draw_update_notice(layout, context)
            box = layout.box()
            row = box.row()
            if hasattr(p, "bl_rna"):
                row.prop(p, "username", text="Pseudo")
                row.prop(p, "color", text="")
            else:
                row.label(text="Pseudo : %s" % p.username)
            online_ok = bool(getattr(bpy.app, "online_access", True))
            box = layout.box()
            box.label(text="Heberger", icon="NETWORK_DRIVE")
            box.prop(mp, "session_name")
            box.prop(mp, "online")
            if mp.online and not online_ok:
                _draw_online_access(box, context)
            row = box.row(align=True)
            row.prop(mp, "port")
            col = box.column(align=True)
            col.label(text="Mot de passe (genere si vide) :" if mp.online else "Mot de passe (facultatif) :")
            col.prop(mp, "password", text="")
            box.prop(mp, "sync_frame")
            box.operator("mp.host", icon="PLAY")

            box = layout.box()
            box.label(text="Rejoindre", icon="URL")
            link = _clipboard_invite(context)
            row = box.row()
            row.scale_y = 1.5
            if link is not None:
                row.operator("mp.join_link", text="Rejoindre \"%s\"" % (link["name"] or "la session"),
                             icon="LINKED")
            else:
                row.operator("mp.join_link", icon="PASTEDOWN")
            if not online_ok:
                _draw_online_access(box, context)
            col = box.column(align=True)
            col.label(text="Ou saisir l'adresse :")
            col.prop(mp, "address", text="")
            row = col.row(align=True)
            row.prop(mp, "port")
            col.label(text="Mot de passe :")
            col.prop(mp, "password", text="")
            box.prop(mp, "as_viewer")
            box.operator("mp.join", icon="LINKED")
            last = getattr(sess, "last_reason", "")
            if last:
                import textwrap
                width = max(20, int(context.region.width / (7.0 * context.preferences.system.ui_scale)))
                col = layout.box().column(align=True)
                for i, line in enumerate(textwrap.wrap(last, width)):
                    col.label(text=line, icon="ERROR" if i == 0 else "BLANK1")
            return
        box = layout.box()
        if s.is_host:
            box.label(text="Hote : %s" % s.session_name, icon="NETWORK_DRIVE")
            row = box.row()
            row.scale_y = 1.5
            row.operator("mp.copy_invite", icon="COPYDOWN")
            t = s.tunnel
            if t is not None:
                pub = s.public_address()
                if pub:
                    row = box.row(align=True)
                    row.label(text="Internet : %s" % pub, icon="WORLD")
                    op = row.operator("mp.copy_address", text="", icon="COPYDOWN")
                    op.address = pub
                elif t.state == "retry":
                    box.label(text="Internet indisponible, nouvel essai... (%s)" % t.error[:60],
                              icon="ERROR")
                else:
                    box.label(text="Ouverture de l'adresse Internet...", icon="TIME")
            for ip in net.local_addresses()[:2]:
                row = box.row(align=True)
                addr = "%s:%d" % (ip, s.port)
                row.label(text="Reseau local : %s" % addr, icon="NETWORK_DRIVE")
                op = row.operator("mp.copy_address", text="", icon="COPYDOWN")
                op.address = addr
            if s.password:
                box.label(text="Mot de passe : %s" % s.password if s.password_generated
                          else "Protegee par mot de passe", icon="LOCKED")
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
            icon = {"checkpoints": "FILE_TICK", "autosave": "TIME",
                    "archives": "BOOKMARKS"}.get(kind, "FILE_BACKUP")
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


_clip_cache = {"t": 0.0, "text": None, "link": None}


def _clipboard_invite(context):
    """Lien d'invitation present dans le presse-papiers (lu au plus 1x/s)."""
    now = time.time()
    if now - _clip_cache["t"] > 1.0:
        _clip_cache["t"] = now
        try:
            text = context.window_manager.clipboard or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text != _clip_cache["text"]:
            _clip_cache["text"] = text
            _clip_cache["link"] = invite.parse(text) if len(text) < 4096 else None
    return _clip_cache["link"]


def _draw_online_access(layout, context):
    col = layout.column(align=True)
    col.label(text="Acces en ligne de Blender desactive", icon="ERROR")
    col.prop(context.preferences.system, "use_online_access", text="Autoriser l'acces en ligne")


def draw_update_notice(layout, context):
    st = updater.state["status"]
    done = updater.updated_from()
    if st == "available":
        box = layout.box()
        box.label(text="Nouvelle version %s disponible" % updater.state["latest"], icon="INFO")
        box.operator("mp.install_update", icon="IMPORT")
    elif st in ("downloading", "installing"):
        layout.label(text="Mise a jour en cours...", icon="TIME")
    elif done:
        layout.label(text="Addon mis a jour : %s -> %s" % done, icon="CHECKMARK")


def draw_update_status(layout, context):
    st = updater.state
    row = layout.row()
    row.label(text="Version installee : %s" % updater.current_version_str())
    row.operator("mp.check_update", text="", icon="FILE_REFRESH")
    status = st["status"]
    if status == "available":
        row = layout.row()
        row.label(text="Version %s disponible" % st["latest"], icon="INFO")
        row.operator("mp.install_update", icon="IMPORT")
    elif status == "uptodate":
        layout.label(text="A jour", icon="CHECKMARK")
    elif status == "checking":
        layout.label(text="Verification...", icon="TIME")
    elif status in ("downloading", "installing"):
        layout.label(text="Mise a jour en cours...", icon="TIME")
    elif status == "installed":
        layout.label(text="Mise a jour installee", icon="CHECKMARK")
    elif status == "error":
        layout.label(text="Echec : %s" % st["error"][:80], icon="ERROR")
    elif status == "offline":
        layout.label(text="Acces en ligne desactive", icon="ERROR")


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
