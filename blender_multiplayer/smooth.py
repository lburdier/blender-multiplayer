"""Deplacements fluides des objets modifies par les autres joueurs.

Les positions arrivent par paquets (reseau, ~30 par seconde au mieux, avec des
irregularites). Au lieu de sauter d'une position a l'autre, l'objet glisse
vers la nouvelle valeur en un temps egal a l'intervalle entre deux messages :
le mouvement parait continu, a 60 images par seconde.

L'etat "connu" (synchronise) prend tout de suite la valeur finale ; pendant le
glissement, ces champs sont ignores par la detection des changements locaux
(pas d'echo).
"""

import time

import bpy
from mathutils import Quaternion

FIELDS = frozenset(("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle",
                    "scale", "delta_location", "delta_rotation_euler", "delta_scale"))
_STEP = 1.0 / 60.0

_active = []   # lisseurs ayant des glissements en cours


class Smoother:
    def __init__(self, tracker):
        self.tracker = tracker
        self.items = {}        # (uid, champ) -> [depart, cible, t0, duree]
        self.by_uid = {}       # uid -> set(champs)
        self.last_rx = {}      # uid -> heure du dernier message
        self.enabled = True

    def fields_of(self, uid):
        return self.by_uid.get(uid, ())

    def add(self, uid, obj, targets, now=None):
        """targets : {champ: valeur cible}. Retourne les champs pris en charge."""
        now = now or time.time()
        prev = self.last_rx.get(uid)
        self.last_rx[uid] = now
        gap = (now - prev) if prev else 1.0
        # Duree = intervalle entre messages (mouvement continu) ; bornee pour
        # rester reactif. Premier message apres une pause : glissement court.
        dur = 0.08 if gap > 0.5 else min(max(gap * 1.15, 0.035), 0.2)
        taken = []
        for field, target in targets.items():
            try:
                cur = list(getattr(obj, field))
            except Exception:  # noqa: BLE001
                continue
            if len(cur) != len(target):
                continue
            self.items[(uid, field)] = [cur, list(target), now, dur]
            self.by_uid.setdefault(uid, set()).add(field)
            taken.append(field)
        if taken and self not in _active:
            _active.append(self)
            if not bpy.app.timers.is_registered(_timer):
                bpy.app.timers.register(_timer, first_interval=_STEP)
        return taken

    def step(self, now):
        finished = []
        for key, (a, b, t0, dur) in list(self.items.items()):
            uid, field = key
            obj = self.tracker.resolve(uid)
            if obj is None:
                finished.append(key)
                continue
            k = (now - t0) / dur if dur > 0 else 1.0
            if k >= 1.0:
                val = b
                finished.append(key)
            elif field == "rotation_quaternion":
                val = Quaternion(a).slerp(Quaternion(b), k)
            else:
                val = [x + (y - x) * k for x, y in zip(a, b)]
            try:
                setattr(obj, field, val)
            except Exception:  # noqa: BLE001
                finished.append(key)
        for key in finished:
            self.items.pop(key, None)
            uid, field = key
            fields = self.by_uid.get(uid)
            if fields is not None:
                fields.discard(field)
                if not fields:
                    self.by_uid.pop(uid, None)
                    self.tracker.settle(uid)
        return bool(self.items)

    def finish_all(self):
        """Termine tout de suite tous les glissements (valeurs finales exactes)."""
        if self.items:
            for item in self.items.values():
                item[2] = 0.0
            self.step(time.time())

    def cancel(self, uid):
        for field in list(self.by_uid.get(uid, ())):
            self.items.pop((uid, field), None)
        self.by_uid.pop(uid, None)


def _timer():
    now = time.time()
    for sm in list(_active):
        try:
            busy = sm.step(now)
        except Exception:  # noqa: BLE001
            busy = False
        if not busy:
            _active.remove(sm)
    return _STEP if _active else None


def stop_all():
    for sm in list(_active):
        sm.finish_all()
    _active.clear()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
