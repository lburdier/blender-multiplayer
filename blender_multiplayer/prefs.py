"""Preferences de l'addon (pseudo, couleur, sauvegardes, affichage)."""

import getpass
import random

import bpy
from bpy.props import BoolProperty, FloatVectorProperty, IntProperty, StringProperty

ADDON_ID = __package__

_PALETTE = [(0.95, 0.35, 0.25), (0.25, 0.65, 0.98), (0.35, 0.85, 0.40), (0.98, 0.78, 0.20),
            (0.75, 0.45, 0.98), (0.20, 0.85, 0.85), (0.98, 0.45, 0.72)]


def _default_name():
    try:
        return getpass.getuser()[:24] or "Joueur"
    except Exception:
        return "Joueur"


class MP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    username: StringProperty(name="Pseudo", default=_default_name(), maxlen=32)
    color: FloatVectorProperty(name="Couleur", subtype="COLOR", size=3, min=0, max=1,
                               default=random.choice(_PALETTE))
    default_port: IntProperty(name="Port par defaut", default=5555, min=1024, max=65535)
    root_dir: StringProperty(name="Dossier des sessions", subtype="DIR_PATH", default="",
                             description="Vide = ~/BlenderMultiplayer")
    autosave_minutes: IntProperty(name="Autosauvegarde (min)", default=3, min=0, max=120,
                                  description="0 = desactivee")
    max_backups: IntProperty(name="Sauvegardes conservees", default=20, min=1, max=500)
    local_backup: BoolProperty(name="Sauvegardes locales (clients)", default=True,
                               description="Les clients gardent aussi des copies de la scene")
    backup_before_join: BoolProperty(name="Sauvegarder avant de rejoindre", default=True,
                                     description="Copie de votre fichier avant de le remplacer "
                                                 "par la scene de la session")
    override_undo: BoolProperty(name="Ctrl+Z multijoueur", default=True,
                                description="En session, Ctrl+Z annule votre derniere action "
                                            "pour tout le monde sans toucher a celles des autres")
    show_avatars: BoolProperty(name="Cameras des joueurs", default=True)
    show_names: BoolProperty(name="Noms", default=True)
    show_selection: BoolProperty(name="Selections", default=True)
    show_cursors: BoolProperty(name="Curseurs 3D", default=True)
    show_toasts: BoolProperty(name="Messages a l'ecran", default=True)
    relay: StringProperty(name="Serveur relais", default="bore.pub:7835",
                          description="Relais TCP compatible bore utilise pour rendre la session "
                                      "joignable par Internet (bore.pub est public et gratuit ; "
                                      "vous pouvez heberger le votre avec 'bore server')")
    check_updates: BoolProperty(name="Verifier les mises a jour au demarrage", default=True)
    auto_update: BoolProperty(name="Installer automatiquement les mises a jour", default=True,
                              description="Installe la nouvelle version au demarrage de Blender "
                                          "(jamais pendant une session)")

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        row = col.row()
        row.prop(self, "username")
        row.prop(self, "color", text="")
        col.prop(self, "default_port")
        col.prop(self, "root_dir")
        box = layout.box()
        box.label(text="Sauvegardes", icon="FILE_BACKUP")
        box.prop(self, "autosave_minutes")
        box.prop(self, "max_backups")
        box.prop(self, "local_backup")
        box.prop(self, "backup_before_join")
        box = layout.box()
        box.label(text="Comportement et affichage", icon="OVERLAY")
        box.prop(self, "override_undo")
        grid = box.grid_flow(columns=3)
        for p in ("show_avatars", "show_names", "show_selection", "show_cursors", "show_toasts"):
            grid.prop(self, p)
        box = layout.box()
        box.label(text="Internet", icon="WORLD")
        box.prop(self, "relay")
        if not getattr(bpy.app, "online_access", True):
            box.prop(context.preferences.system, "use_online_access",
                     text="Autoriser l'acces en ligne (requis pour Internet et les mises a jour)")
        box = layout.box()
        box.label(text="Mises a jour", icon="FILE_REFRESH")
        box.prop(self, "check_updates")
        box.prop(self, "auto_update")
        from . import ui
        ui.draw_update_status(box, context)


class _Defaults:
    """Valeurs par defaut quand l'addon n'est pas active via le systeme (tests)."""
    username = _default_name()
    color = (0.95, 0.35, 0.25)
    default_port = 5555
    root_dir = ""
    autosave_minutes = 3
    max_backups = 20
    local_backup = True
    backup_before_join = True
    override_undo = True
    show_avatars = True
    show_names = True
    show_selection = True
    show_cursors = True
    show_toasts = True
    relay = "bore.pub:7835"
    check_updates = True
    auto_update = True


_defaults = _Defaults()


def get():
    try:
        addon = bpy.context.preferences.addons.get(ADDON_ID)
        if addon is not None and addon.preferences is not None:
            return addon.preferences
    except Exception:
        pass
    return _defaults


classes = (MP_AddonPreferences,)
