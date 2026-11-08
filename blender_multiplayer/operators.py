"""Operateurs : heberger, rejoindre, annuler/retablir multijoueur, historique,
chat, suivi des joueurs, sauvegardes..."""

import os
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from . import invite, net, prefs, presence, updater
from . import session as sess


def _s():
    return sess.current


class MP_OT_host(bpy.types.Operator):
    bl_idname = "mp.host"
    bl_label = "Heberger une session"
    bl_description = "Ouvre une session multijoueur sur ce Blender (vous etes l'hote)"

    def execute(self, context):
        mp = context.window_manager.mp
        online = mp.online and bool(getattr(bpy.app, "online_access", True))
        try:
            s = sess.host(mp.port, mp.password, mp.session_name or "Session",
                          sync_frame=mp.sync_frame, online=online, relay=prefs.get().relay)
        except OSError as exc:
            self.report({"ERROR"}, "Impossible d'ouvrir le port %d : %s" % (mp.port, exc))
            return {"CANCELLED"}
        moved = (" (port %d deja utilise sur ce PC)" % mp.port) if s.port != mp.port else ""
        if online:
            self.report({"INFO"}, "Session ouverte%s. Copiez le lien d'invitation pour inviter "
                                  "des joueurs." % moved)
        else:
            self.report({"INFO"}, "Session ouverte (reseau local) : %s port %d%s" %
                        (", ".join(net.local_addresses()), s.port, moved))
        return {"FINISHED"}


class MP_OT_join(bpy.types.Operator):
    bl_idname = "mp.join"
    bl_label = "Rejoindre"
    bl_description = ("Rejoint une session : votre scene sera remplacee par celle de l'hote "
                      "(une copie de sauvegarde est faite avant)")

    def invoke(self, context, event):
        if bpy.data.is_dirty:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        mp = context.window_manager.mp
        text = mp.address.strip()
        link = invite.parse(text) if ("#" in text or "/" in text) else None
        if link is not None:
            return _join_link(self, link, mp.as_viewer)
        host, port = invite.parse_address(text, mp.port)
        if not host:
            self.report({"ERROR"}, "Saisissez l'adresse donnee par l'hote, ou utilisez son lien "
                                   "d'invitation (bouton \"Rejoindre avec le lien copie\").")
            return {"CANCELLED"}
        try:
            sess.join(host, port, mp.password, viewer=mp.as_viewer)
        except OSError as exc:
            self.report({"ERROR"}, "Connexion impossible a %s:%d : %s" % (
                host, port, sess.describe_error(exc, host)))
            return {"CANCELLED"}
        return {"FINISHED"}


def _join_link(op, link, viewer):
    if not getattr(bpy.app, "online_access", True) and \
            not all(invite.is_private(h) for h, _p in link["addresses"]):
        op.report({"ERROR"}, "L'acces en ligne de Blender est desactive : cochez \"Autoriser "
                             "l'acces en ligne\" dans l'onglet Multijoueur, puis recommencez.")
        return {"CANCELLED"}
    try:
        sess.join_any(link["addresses"], link["password"], viewer=viewer)
    except OSError as exc:
        op.report({"ERROR"}, "Impossible de rejoindre \"%s\" : %s. L'hote a peut-etre ferme la "
                             "session ; demandez-lui un nouveau lien." % (link["name"], exc))
        return {"CANCELLED"}
    op.report({"INFO"}, "Connexion a \"%s\"..." % link["name"])
    return {"FINISHED"}


class MP_OT_join_link(bpy.types.Operator):
    bl_idname = "mp.join_link"
    bl_label = "Rejoindre avec le lien copie"
    bl_description = ("Rejoint la session du lien d'invitation copie dans le presse-papiers "
                      "(votre scene sera remplacee par celle de l'hote, une copie est faite avant)")
    link: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if not self.link:
            self.link = context.window_manager.clipboard or ""
        if invite.parse(self.link) is None:
            self.report({"ERROR"}, "Aucun lien d'invitation dans le presse-papiers : copiez le lien "
                                   "envoye par l'hote, puis cliquez a nouveau.")
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        link = invite.parse(self.link or context.window_manager.clipboard or "")
        if link is None:
            self.report({"ERROR"}, "Lien d'invitation invalide.")
            return {"CANCELLED"}
        return _join_link(self, link, context.window_manager.mp.as_viewer)


class MP_OT_copy_invite(bpy.types.Operator):
    bl_idname = "mp.copy_invite"
    bl_label = "Copier le lien d'invitation"
    bl_description = ("Copie un lien prive a envoyer aux joueurs : il contient l'adresse et le mot "
                      "de passe de la session. Ne le partagez qu'avec les personnes invitees")

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host:
            return {"CANCELLED"}
        context.window_manager.clipboard = s.invite_link()
        if s.tunnel is not None and not s.public_address():
            self.report({"WARNING"}, "Lien copie, mais l'adresse Internet n'est pas encore prete : "
                                     "il ne marche pour l'instant que sur votre reseau local.")
        else:
            self.report({"INFO"}, "Lien d'invitation copie : envoyez-le aux joueurs.")
        return {"FINISHED"}


class MP_OT_check_update(bpy.types.Operator):
    bl_idname = "mp.check_update"
    bl_label = "Verifier les mises a jour"
    bl_description = "Cherche une nouvelle version de l'addon"

    def execute(self, context):
        if not updater.online_allowed():
            self.report({"ERROR"}, "Autorisez d'abord l'acces en ligne de Blender.")
            return {"CANCELLED"}
        updater.check(auto_install=False)
        return {"FINISHED"}


class MP_OT_install_update(bpy.types.Operator):
    bl_idname = "mp.install_update"
    bl_label = "Mettre a jour"
    bl_description = "Telecharge et installe la nouvelle version de l'addon"

    def execute(self, context):
        err = updater.install()
        if err:
            self.report({"WARNING"}, err)
            return {"CANCELLED"}
        self.report({"INFO"}, "Mise a jour en cours...")
        return {"FINISHED"}


class MP_OT_leave(bpy.types.Operator):
    bl_idname = "mp.leave"
    bl_label = "Quitter la session"
    bl_description = "Quitte la session (la scene reste dans votre Blender)"

    def invoke(self, context, event):
        s = _s()
        if s is not None and s.is_host and len(s.users) > 1:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        sess.leave()
        return {"FINISHED"}


class MP_OT_undo(bpy.types.Operator):
    bl_idname = "mp.undo"
    bl_label = "Annuler (multijoueur)"
    bl_description = "Annule votre derniere action pour tous les joueurs"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        s = _s()
        obj = context.active_object
        if (s is None or not s.active or not prefs.get().override_undo
                or (obj is not None and obj.mode != "OBJECT")):
            # Hors session ou en mode Edition : annulation native (objet verrouille par nous).
            return {"PASS_THROUGH"}
        s.undo()
        return {"FINISHED"}


class MP_OT_redo(bpy.types.Operator):
    bl_idname = "mp.redo"
    bl_label = "Retablir (multijoueur)"
    bl_description = "Retablit votre derniere action annulee"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        s = _s()
        obj = context.active_object
        if (s is None or not s.active or not prefs.get().override_undo
                or (obj is not None and obj.mode != "OBJECT")):
            return {"PASS_THROUGH"}
        s.redo()
        return {"FINISHED"}


class MP_OT_history_undo(bpy.types.Operator):
    bl_idname = "mp.history_undo"
    bl_label = "Annuler cette action"
    bl_description = ("Annule uniquement cette action (les modifications faites ensuite par "
                      "d'autres sur les memes proprietes sont conservees)")
    seq: IntProperty()

    def execute(self, context):
        s = _s()
        if s is None or not s.active:
            return {"CANCELLED"}
        s.hist_undo(self.seq)
        return {"FINISHED"}


class MP_OT_history_revert(bpy.types.Operator):
    bl_idname = "mp.history_revert"
    bl_label = "Revenir a ce point"
    bl_description = "Ramene toute la scene a l'etat qu'elle avait juste apres cette action"
    seq: IntProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = _s()
        if s is None or not s.active:
            return {"CANCELLED"}
        s.hist_revert(self.seq)
        return {"FINISHED"}


class MP_OT_chat(bpy.types.Operator):
    bl_idname = "mp.chat"
    bl_label = "Envoyer"
    bl_description = "Envoie le message a tous les joueurs"

    def execute(self, context):
        s = _s()
        mp = context.window_manager.mp
        if s is None or not mp.chat_text.strip():
            return {"CANCELLED"}
        s.send_chat(mp.chat_text)
        mp.chat_text = ""
        return {"FINISHED"}


class MP_OT_mark(bpy.types.Operator):
    bl_idname = "mp.mark"
    bl_label = "Signaler le curseur"
    bl_description = "Montre a tous les joueurs l'emplacement du curseur 3D"

    def execute(self, context):
        s = _s()
        if s is None or not s.active:
            return {"CANCELLED"}
        s.send_mark(tuple(context.scene.cursor.location))
        return {"FINISHED"}


class MP_OT_follow(bpy.types.Operator):
    bl_idname = "mp.follow"
    bl_label = "Suivre"
    bl_description = "Votre vue suit celle de ce joueur (bougez la vue pour arreter)"
    user: IntProperty()

    def execute(self, context):
        s = _s()
        if s is None:
            return {"CANCELLED"}
        if s.follow == self.user:
            s.follow = None
        else:
            s.start_follow(self.user)
        return {"FINISHED"}


class MP_OT_goto(bpy.types.Operator):
    bl_idname = "mp.goto"
    bl_label = "Aller a"
    bl_description = "Place votre vue a l'endroit ou regarde ce joueur"
    user: IntProperty()

    def execute(self, context):
        s = _s()
        if s is None or self.user not in s.users:
            return {"CANCELLED"}
        presence.goto(s.users[self.user].get("presence"))
        return {"FINISHED"}


class MP_OT_kick(bpy.types.Operator):
    bl_idname = "mp.kick"
    bl_label = "Exclure"
    bl_description = "Exclut ce joueur de la session"
    user: IntProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host:
            return {"CANCELLED"}
        s.kick(self.user)
        return {"FINISHED"}


class MP_OT_role(bpy.types.Operator):
    bl_idname = "mp.role"
    bl_label = "Changer le role"
    bl_description = "Bascule ce joueur entre editeur et spectateur (lecture seule)"
    user: IntProperty()

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host or self.user not in s.users:
            return {"CANCELLED"}
        cur = s.users[self.user].get("role")
        s.set_role(self.user, "editor" if cur == "viewer" else "viewer")
        return {"FINISHED"}


class MP_OT_checkpoint(bpy.types.Operator):
    bl_idname = "mp.checkpoint"
    bl_label = "Point de sauvegarde"
    bl_description = "Enregistre maintenant une copie complete de la scene de la session"

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host:
            return {"CANCELLED"}
        path = s.checkpoint(context.window_manager.mp.checkpoint_label)
        context.window_manager.mp.checkpoint_label = ""
        self.report({"INFO"}, "Sauvegarde : %s" % path)
        return {"FINISHED"}


class MP_OT_restore(bpy.types.Operator):
    bl_idname = "mp.restore"
    bl_label = "Restaurer"
    bl_description = ("Recharge cette sauvegarde pour TOUS les joueurs (l'etat actuel est "
                      "sauvegarde avant)")
    filepath: StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host:
            return {"CANCELLED"}
        s.restore_file(self.filepath)
        return {"FINISHED"}


class MP_OT_resync(bpy.types.Operator):
    bl_idname = "mp.resync"
    bl_label = "Resynchroniser"
    bl_description = "Recharge entierement la scene depuis l'hote (en cas de doute)"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = _s()
        if s is None or s.is_host:
            return {"CANCELLED"}
        s.request_resync()
        return {"FINISHED"}


class MP_OT_open_folder(bpy.types.Operator):
    bl_idname = "mp.open_folder"
    bl_label = "Ouvrir le dossier"
    bl_description = "Ouvre le dossier de la session (sauvegardes, historique)"

    def execute(self, context):
        s = _s()
        path = s.session_dir if s is not None and s.session_dir else sess.root_dir()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, str(exc))
        return {"FINISHED"}


class MP_OT_copy_address(bpy.types.Operator):
    bl_idname = "mp.copy_address"
    bl_label = "Copier l'adresse"
    bl_description = "Copie l'adresse et le port a communiquer aux autres joueurs"
    address: StringProperty()

    def execute(self, context):
        context.window_manager.clipboard = self.address
        self.report({"INFO"}, "Copie : %s" % self.address)
        return {"FINISHED"}


class MP_OT_sync_frame(bpy.types.Operator):
    bl_idname = "mp.sync_frame"
    bl_label = "Synchroniser la frame"
    bl_description = "Partage la frame courante de la timeline entre tous les joueurs"
    value: BoolProperty()

    def execute(self, context):
        s = _s()
        if s is None or not s.is_host:
            return {"CANCELLED"}
        s.set_sync_frame(self.value)
        return {"FINISHED"}


classes = (MP_OT_host, MP_OT_join, MP_OT_leave, MP_OT_undo, MP_OT_redo, MP_OT_history_undo,
           MP_OT_history_revert, MP_OT_chat, MP_OT_mark, MP_OT_follow, MP_OT_goto, MP_OT_kick,
           MP_OT_role, MP_OT_checkpoint, MP_OT_restore, MP_OT_resync, MP_OT_open_folder,
           MP_OT_copy_address, MP_OT_sync_frame, MP_OT_join_link, MP_OT_copy_invite,
           MP_OT_check_update, MP_OT_install_update)

_keymaps = []


def register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    # "Window" est evalue avant "Screen" (ou se trouve ed.undo) : notre Ctrl+Z
    # passe en premier et laisse passer l'evenement hors session.
    for km_name, space in (("Window", "EMPTY"), ("Object Mode", "EMPTY")):
        km = kc.keymaps.new(name=km_name, space_type=space)
        _keymaps.append((km, km.keymap_items.new("mp.undo", "Z", "PRESS", ctrl=True)))
        _keymaps.append((km, km.keymap_items.new("mp.redo", "Z", "PRESS", ctrl=True, shift=True)))
        _keymaps.append((km, km.keymap_items.new("mp.redo", "Y", "PRESS", ctrl=True)))


def unregister_keymaps():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
