"""Canal temps reel : ce qui bouge pendant un geste, envoye au fil de l'eau.

Le chemin normal (lecture complete, operation, historique) renvoie tout le
maillage ou tout le materiau : trop lourd pour suivre la souris. Pendant un
geste, on envoie donc seulement ce qui a change depuis l'image precedente :

- sommets deplaces en mode Edition ou Sculpt ;
- valeurs des nodes des materiaux et mondes.

Ces messages sont ephemeres : ni historique, ni accuse de reception. Une fois
le geste termine (`SETTLE`), le chemin normal envoie l'etat complet, qui devient
l'etat de reference et l'unique entree d'historique. Un message perdu ou une
topologie modifiee ne peuvent donc jamais laisser de difference durable.

Tant qu'un flux est actif, la detection normale met l'ID de cote (`holding`) :
ni renvoi en double chez celui qui edite, ni echo chez ceux qui recoivent.
"""

import time

import bpy
import numpy as np

from . import compat, ids

MIN_INTERVAL = 1.0 / 60.0
COST_RATIO = 4.0        # au plus 1/4 du temps du fil principal pour lire ou appliquer
SETTLE = 0.35           # silence apres lequel le geste est considere comme fini
RX_HOLD = 1.5           # detection coupee chez celui qui recoit, le temps de l'etat final
MAX_QUEUED = 16         # messages en attente au-dela desquels on regroupe les images
NODE_OWNERS = ("materials", "worlds")


class _MeshTx:
    __slots__ = ("base", "next")

    def __init__(self):
        self.base = None
        self.next = 0.0


def _read_nodes(tree):
    """{(node, 0 entree / 1 sortie, index): valeur} des sockets numeriques non pilotes."""
    driven = compat.animated_paths(tree.animation_data) if tree.animation_data else None
    out = {}
    for node in tree.nodes:
        for kind, sockets in enumerate((node.inputs, node.outputs)):
            for i, sock in enumerate(sockets):
                try:
                    val = sock.default_value
                except AttributeError:
                    continue
                if isinstance(val, str) or isinstance(val, bpy.types.ID):
                    continue
                if not isinstance(val, (bool, int, float)):
                    try:
                        val = [float(x) for x in val]
                    except (TypeError, ValueError):
                        continue
                if driven and kind == 0:
                    path = 'nodes["%s"].inputs[%d].default_value' % (
                        node.name.replace("\\", "\\\\").replace('"', '\\"'), i)
                    if path in driven:
                        continue
                out[(node.name, kind, i)] = val
    return out


class Live:
    def __init__(self, tracker):
        self.tr = tracker
        self.enabled = True
        self.activity = set()   # uid modifies depuis la derniere lecture du flux
        self.mesh_tx = {}       # uid maillage -> _MeshTx
        self.node_tx = {}       # uid materiau / monde -> valeurs envoyees
        self.tx_hold = {}       # uid -> fin du geste en cours chez moi
        self.rx_hold = {}       # uid -> fin de la mise de cote chez moi
        self.rx_mesh = {}       # uid -> [(n, cle de forme, indices, positions)]
        self.rx_next = {}       # uid -> prochaine application autorisee
        self.prime = []         # materiaux dont il reste a noter les valeurs de reference
        self.prime_t = 0.0

    # ------------------------------------------------------------------
    def holding(self, uid, now, full=False):
        """Vrai si la detection normale doit laisser cet ID de cote pour l'instant.

        Une lecture complete demandee (annulation, sauvegarde...) doit voir l'etat
        reel de ce qu'on edite ; seul ce qui vient d'un autre reste mis de cote.
        """
        if now < self.rx_hold.get(uid, 0.0):
            return True
        return not full and now < self.tx_hold.get(uid, 0.0)

    def note_remote(self, uid):
        """Etat complet recu : les valeurs de reference locales sont perimees."""
        self.node_tx.pop(uid, None)

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    def tick(self, now):
        if not self.enabled:
            self.activity.clear()
            return
        session = self.tr.session
        if session.live_backlog() >= MAX_QUEUED:
            return      # reseau en retard : l'image suivante regroupera les changements
        self._tick_meshes(now, session)
        self._tick_nodes(now, session)
        self._prime_nodes(now)
        for hold in (self.tx_hold, self.rx_hold):
            for uid in [u for u, t in hold.items() if t <= now]:
                del hold[uid]

    def _tick_meshes(self, now, session):
        seen = set()
        for obj in self.tr.objects_in_mode(now):
            try:
                mode, me = obj.mode, obj.data
            except ReferenceError:
                continue
            if mode not in ("EDIT", "SCULPT") or not isinstance(me, bpy.types.Mesh):
                continue
            uid, ouid = ids.get_uid(me), ids.get_uid(obj)
            if uid is None:
                continue
            seen.add(uid)
            st = self.mesh_tx.get(uid)
            if st is None:
                st = self.mesh_tx[uid] = _MeshTx()
            active = uid in self.activity or ouid in self.activity
            if mode == "EDIT" and st.base is not None and not active:
                continue
            if now < st.next:
                continue
            self.activity.discard(uid)
            self.activity.discard(ouid)
            t0 = time.perf_counter()
            sent = self._read_mesh(obj, me, mode, uid, ouid, st, session, now)
            cost = time.perf_counter() - t0
            st.next = now + (max(MIN_INTERVAL, cost * COST_RATIO) if sent else
                             max(0.1, cost * COST_RATIO * 2.5))
            if sent:
                self.tx_hold[uid] = now + max(SETTLE, cost * COST_RATIO * 3.0)
        for uid in [u for u in self.mesh_tx if u not in seen]:
            del self.mesh_tx[uid]

    def _read_mesh(self, obj, me, mode, uid, ouid, st, session, now):
        if mode == "EDIT":
            obj.update_from_editmode()
            self.tr.ignore_next.add(uid)
            if ouid:
                self.tr.ignore_next.add(ouid)
        key = obj.active_shape_key if me.shape_keys is not None else None
        source = key.data if key is not None else me.vertices
        n = len(source)
        co = np.empty(n * 3, np.float32)
        source.foreach_get("co", co)
        if st.base is None or st.base.size != co.size:
            st.base = co    # premiere image ou topologie changee : le chemin normal s'en charge
            return False
        moved = np.flatnonzero(np.any(co.reshape(-1, 3) != st.base.reshape(-1, 3), axis=1))
        if moved.size == 0:
            return False
        st.base = co
        msg = {"type": "live", "w": "m", "u": uid, "n": n, "sk": key.name if key is not None else None}
        if moved.size > n * 0.75:
            msg["c"] = -1
            binary = co.tobytes()
        else:
            msg["c"] = int(moved.size)
            binary = moved.astype(np.int32).tobytes() + co.reshape(-1, 3)[moved].tobytes()
        session.send_live(msg, binary)
        session.last_change_t = now
        return True

    def _tick_nodes(self, now, session):
        for uid in [u for u in self.activity if self.tr.attr_of.get(u) in NODE_OWNERS]:
            self.activity.discard(uid)
            if now < self.rx_hold.get(uid, 0.0):
                self.node_tx.pop(uid, None)     # c'est un autre joueur qui fait bouger ces valeurs
                continue
            idb = self.tr.index.get(uid)
            try:
                tree = idb.node_tree if idb is not None else None
                if tree is None:
                    continue
                cur = _read_nodes(tree)
            except ReferenceError:
                continue
            base = self.node_tx.get(uid)
            self.node_tx[uid] = cur
            if base is None:
                continue
            values = [[k[0], k[1], k[2], v] for k, v in cur.items() if k in base and base[k] != v]
            if values:
                session.send_live({"type": "live", "w": "n", "u": uid, "v": values})
                session.last_change_t = now
                self.tx_hold[uid] = now + SETTLE

    def _prime_nodes(self, now):
        """Note les valeurs de reference, un materiau par tic, avant tout geste.

        Sans elles, la premiere image d'un geste ne pourrait pas etre comparee et
        partirait par le chemin normal, avec une entree d'historique en trop.
        """
        if not self.prime:
            if now >= self.prime_t:
                self.prime_t = now + 2.0
                self.prime = [u for u, a in self.tr.attr_of.items()
                              if a in NODE_OWNERS and u not in self.node_tx]
            return
        uid = self.prime.pop()
        if uid in self.node_tx or uid in self.activity or now < self.rx_hold.get(uid, 0.0):
            return
        idb = self.tr.index.get(uid)
        try:
            tree = idb.node_tree if idb is not None else None
            if tree is not None:
                self.node_tx[uid] = _read_nodes(tree)
        except ReferenceError:
            pass

    # ------------------------------------------------------------------
    # Reception
    # ------------------------------------------------------------------
    def receive(self, msg, binary):
        uid = msg.get("u")
        now = time.time()
        if msg.get("w") == "m":
            n, count = int(msg["n"]), int(msg["c"])
            if count < 0:
                idx, co = None, np.frombuffer(binary, np.float32, n * 3)
            else:
                idx = np.frombuffer(binary, np.int32, count)
                co = np.frombuffer(binary, np.float32, count * 3, offset=count * 4).reshape(-1, 3)
            self.rx_mesh.setdefault(uid, []).append((n, msg.get("sk"), idx, co))
            self.rx_hold[uid] = now + RX_HOLD
            self.tr.session.last_change_t = now
        elif msg.get("w") == "n":
            self.rx_hold[uid] = now + RX_HOLD
            self.tr.session.last_change_t = now
            self._apply_nodes(uid, msg.get("v") or ())

    def _apply_nodes(self, uid, values):
        idb = self.tr.resolve(uid)
        tree = getattr(idb, "node_tree", None) if idb is not None else None
        if tree is None:
            return
        for name, kind, i, val in values:
            node = tree.nodes.get(name)
            if node is None:
                continue
            sockets = node.outputs if kind else node.inputs
            try:
                sockets[i].default_value = val
            except (IndexError, TypeError, ValueError, AttributeError):
                continue
        tree.update_tag()
        idb.update_tag()
        compat.tag_redraw(("VIEW_3D", "NODE_EDITOR"))

    def flush(self, now):
        """Applique les sommets recus, en regroupant ce qui est arrive entre deux images."""
        for uid in list(self.rx_mesh):
            if now < self.rx_next.get(uid, 0.0):
                continue
            items = self.rx_mesh.pop(uid)
            t0 = time.perf_counter()
            try:
                self._apply_mesh(uid, items)
            except ReferenceError:
                continue
            self.rx_next[uid] = now + max(MIN_INTERVAL, (time.perf_counter() - t0) * COST_RATIO)

    def _apply_mesh(self, uid, items):
        me = self.tr.resolve(uid)
        if not isinstance(me, bpy.types.Mesh):
            return
        if me.is_editmode:
            # Un autre edite le meme maillage que nous : ses sommets arrivent
            # directement dans notre BMesh d'edition.
            from . import coedit
            coedit.apply_live(me, items, self, uid)
            compat.tag_redraw(("VIEW_3D",))
            return
        by_key = {}
        for item in items:
            by_key.setdefault(item[1], []).append(item)
        keys = me.shape_keys
        for name, group in by_key.items():
            block = keys.key_blocks.get(name) if keys is not None and name else None
            if keys is not None and name and block is None:
                continue
            targets = [block.data] if block is not None else [me.vertices]
            if block is not None and block == keys.reference_key:
                targets.append(me.vertices)
            nv = len(targets[0])
            co = np.empty(nv * 3, np.float32)
            targets[0].foreach_get("co", co)
            patched = False
            for n, _name, idx, new in group:
                if n != nv:
                    continue        # topologie differente : l'etat complet arrive
                if idx is None:
                    co = new.copy()
                else:
                    co.reshape(-1, 3)[idx] = new
                patched = True
            if patched:
                for target in targets:
                    target.foreach_set("co", co)
        if keys is not None:
            keys.update_tag()
        me.update()
        compat.tag_redraw(("VIEW_3D",))
