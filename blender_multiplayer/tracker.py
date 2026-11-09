"""Suivi des data-blocks : detection des changements locaux et application
des changements distants.

Principe anti-echo : on garde pour chaque UID le dernier etat "connu"
(synchronise). Un changement local = difference entre l'etat lu maintenant
et l'etat connu. Apres avoir applique un changement distant, on relit
l'etat et on le memorise comme connu : il ne sera donc jamais renvoye.
"""

import time

import bpy

from . import animmask, compat, ids, live, payloads, serialize, smooth


class Tracker:
    def __init__(self, session):
        self.session = session
        self.store = session.store
        self.known = {}        # uid -> {"t": attr, "f": {champ: valeur}}
        self.index = {}        # uid -> data-block
        self.attr_of = {}      # uid -> collection bpy.data
        self.ptr2uid = {}      # pointeur -> uid
        self.uid_ptr = {}      # uid -> pointeur (dernier scan)
        self.embedded = {}     # pointeur node_tree / master collection -> uid proprietaire
        self.mesh_vg = set()   # pointeurs de maillages dont un objet a des groupes de vertex
        self.mesh_users = {}   # pointeur maillage -> [objets]
        self.dirty = {}        # uid -> set de drapeaux ("x", "g")
        self.deferred = []     # [t, uid, attr, champ, valeur]
        self.deferred_creates = []
        self.wanted_names = {}
        self.mode_activity = set()
        self.ignore_next = set()
        self.tombstones = set()
        self.need_scan = True
        self.stale = True
        self.last_scan = 0.0
        self.rr = []
        self.rr_pos = 0
        self.blob_last = {}
        self.edit_last = {}
        self.mode_objects = []
        self.mode_scan_t = 0.0
        self.last_op_ptr = None
        self.sync_frame = False
        self.edit_interval = 0.3
        self.blob_interval = 0.25
        self.px_known = {}     # uid image -> empreinte des derniers pixels synchronises
        self.px_state = {}     # uid image -> [empreinte vue, heure d'envoi, heure de lecture]
        self.px_t = 0.0
        self.mesh_last = {}    # uid maillage -> heure de la derniere lecture complete
        self.obj_last = {}     # uid objet -> heure de la derniere lecture complete
        self.budget = 0.006    # secondes de lectures completes par tick (le reste attend)
        self.drv_targets = {}  # pointeur d'ID -> [heure, pointeurs des cibles de ses drivers]
        self.waiting = {}      # uid -> heure ou sa lecture a ete repoussee faute de budget
        self.smoother = smooth.Smoother(self)
        self.live = live.Live(self)
        try:
            from . import prefs
            self.smoother.enabled = bool(getattr(prefs.get(), "smooth_motion", True))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Interface utilisee par serialize (ctx)
    # ------------------------------------------------------------------
    def log(self, text):
        self.session.log(text)

    def assets_dir(self):
        return self.session.assets_dir()

    def resolve(self, uid):
        idb = self.index.get(uid)
        if idb is not None:
            try:
                if ids.get_uid(idb) == uid:
                    return idb
            except ReferenceError:
                pass
            self.index.pop(uid, None)
            self.stale = True
        if self.stale:
            self.scan()
            return self.index.get(uid)
        return None

    def register(self, uid, idb, attr):
        self.index[uid] = idb
        self.attr_of[uid] = attr
        ptr = idb.as_pointer()
        self.ptr2uid[ptr] = uid
        self.uid_ptr[uid] = ptr

    def adopt(self, uid, idb, attr):
        """Data-block arrive comme dependance d'un blob : connu, pas renvoye."""
        self.register(uid, idb, attr)
        try:
            f = serialize.read(attr, idb, self, deep=True)
        except Exception:
            f = {"name": idb.name}
        if self.session.is_host and ids.mode_of(attr) == "blob":
            try:
                f.update(serialize.write_blob(idb, attr, self))
            except Exception as exc:  # noqa: BLE001
                self.log("blob %s: %s" % (idb.name, exc))
        self.known[uid] = {"t": attr, "f": f}
        self.tombstones.discard(uid)

    def defer(self, uid, attr, k, v):
        self.deferred.append([time.time(), uid, attr, k, v])

    def mesh_has_vgroups(self, me):
        return me.as_pointer() in self.mesh_vg

    def users_of(self, me):
        return self.mesh_users.get(me.as_pointer(), [])

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def scan(self):
        index, attr_of, ptr2uid, uid_ptr = {}, {}, {}, {}
        embedded, mesh_vg, mesh_users = {}, set(), {}
        for attr in ids.active_types():
            coll = getattr(bpy.data, attr, None)
            if coll is None:
                continue
            for idb in coll:
                if not ids.trackable(attr, idb):
                    continue
                ptr = idb.as_pointer()
                uid = ids.get_uid(idb)
                if uid is not None and uid in index:
                    # UID duplique (Maj+D copie les proprietes) : l'original le garde.
                    if self.uid_ptr.get(uid) == ptr:
                        other = index[uid]
                        nu = ids.new_uid()
                        ids.set_uid(other, nu)
                        optr = other.as_pointer()
                        index[nu] = other
                        attr_of[nu] = attr_of[uid]
                        ptr2uid[optr] = nu
                        uid_ptr[nu] = optr
                    else:
                        uid = None
                if uid is None:
                    uid = ids.new_uid()
                    ids.set_uid(idb, uid)
                index[uid] = idb
                attr_of[uid] = attr
                ptr2uid[ptr] = uid
                uid_ptr[uid] = ptr
                nt = getattr(idb, "node_tree", None)
                if nt is not None:
                    try:
                        embedded[nt.as_pointer()] = uid
                    except Exception:
                        pass
                if attr == "scenes":
                    embedded[idb.collection.as_pointer()] = uid
                elif attr == "objects" and idb.type == "MESH" and idb.data is not None:
                    dptr = idb.data.as_pointer()
                    mesh_users.setdefault(dptr, []).append(idb)
                    if len(idb.vertex_groups):
                        mesh_vg.add(dptr)
        self.index, self.attr_of, self.ptr2uid, self.uid_ptr = index, attr_of, ptr2uid, uid_ptr
        self.embedded, self.mesh_vg, self.mesh_users = embedded, mesh_vg, mesh_users
        self.need_scan = False
        self.stale = False
        self.last_scan = time.time()
        self.rr = sorted(index.keys(), key=lambda u: ids.order_of(attr_of[u]))
        if self.rr_pos >= len(self.rr):
            self.rr_pos = 0

    def init_known(self, write_blobs=False, progress=None):
        """Etat initial : tout ce qui existe est considere comme synchronise."""
        self.scan()
        self.known = {}
        total = len(self.index)
        for i, (uid, idb) in enumerate(list(self.index.items())):
            attr = self.attr_of[uid]
            try:
                f = serialize.read(attr, idb, self, deep=True)
                if write_blobs and (ids.mode_of(attr) == "blob" or f.get("_mode") == "b"):
                    f.update(serialize.write_blob(idb, attr, self))
            except Exception as exc:  # noqa: BLE001
                self.log("lecture %s: %s" % (getattr(idb, "name", uid), exc))
                f = {"name": getattr(idb, "name", "")}
            self.known[uid] = {"t": attr, "f": f}
            if progress is not None and i % 25 == 0:
                progress(i, total)
        self.dirty.clear()

    # ------------------------------------------------------------------
    # Depsgraph
    # ------------------------------------------------------------------
    def _driver_targets(self, idb, now):
        """Pointeurs des data-blocks lus par les drivers de `idb` (et de son
        arbre de nodes, de ses cles de forme). Memorise 2 s."""
        ptr = idb.as_pointer()
        hit = self.drv_targets.get(ptr)
        if hit is not None and now - hit[0] < 2.0:
            return hit[1]
        out = set()
        for holder in (idb, getattr(idb, "node_tree", None), getattr(idb, "shape_keys", None)):
            ad = getattr(holder, "animation_data", None) if holder is not None else None
            if ad is None:
                continue
            for fc in ad.drivers:
                try:
                    for v in fc.driver.variables:
                        for t in v.targets:
                            if t.id is not None:
                                out.add(t.id.as_pointer())
                except Exception:  # noqa: BLE001
                    continue
        if len(self.drv_targets) > 50000:
            self.drv_targets.clear()
        self.drv_targets[ptr] = [now, out]
        return out

    def on_depsgraph(self, depsgraph):
        try:
            updates = list(depsgraph.updates)
        except Exception:
            return
        batch = set()
        for upd in updates:
            try:
                batch.add((getattr(upd.id, "original", None) or upd.id).as_pointer())
            except Exception:
                continue
        now = time.time()
        # Meme frame que lors de la derniere lecture : ce n'est pas la lecture
        # de l'animation qui a provoque ces mises a jour.
        try:
            sc = compat.current_scene()
            fr = (sc.name, sc.frame_current, round(sc.frame_subframe, 3)) if sc is not None else None
        except Exception:  # noqa: BLE001
            fr = None
        static = fr is not None and fr == getattr(self, "_last_frame", None)
        for upd in updates:
            try:
                idb = upd.id
                orig = getattr(idb, "original", None) or idb
                ptr = orig.as_pointer()
            except Exception:
                continue
            uid = self.ptr2uid.get(ptr) or self.embedded.get(ptr)
            if uid is None:
                if isinstance(orig, (bpy.types.Object, bpy.types.Mesh, bpy.types.Material,
                                     bpy.types.Collection, bpy.types.NodeTree)):
                    self.need_scan = True
                continue
            if not isinstance(orig, bpy.types.Object) and uid not in self.dirty:
                # Materiau, lumiere, maillage... recalcule parce qu'un de ses
                # drivers lit un data-block qui vient de changer (ex. feux pilotes
                # par le rig) : ce n'est pas une modification, ses valeurs
                # pilotees sont hors empreinte. Le relire a chaque fois coutait
                # jusqu'a 200 ms par lecture pendant qu'un addon anime le rig.
                try:
                    targets = self._driver_targets(orig, now)
                except ReferenceError:
                    targets = ()
                if targets and not targets.isdisjoint(batch - {ptr}):
                    continue
            flags = self.dirty.setdefault(uid, set())
            if uid not in self.ignore_next:
                self.mode_activity.add(uid)
                self.live.activity.add(uid)
            geom = getattr(upd, "is_updated_geometry", False)
            if isinstance(orig, bpy.types.Object):
                # Un objet qui ne fait que bouger (ou suit son parent / une
                # contrainte) : lecture rapide de sa transformation seulement.
                # La geometrie evaluee d'un objet ne dit rien de son maillage :
                # quand le maillage change, Blender le signale lui-meme.
                if getattr(upd, "is_updated_transform", False) and not geom \
                        and not getattr(upd, "is_updated_shading", False):
                    flags.add("t")
                else:
                    flags.add("x")
            else:
                flags.add("x")
                if geom:
                    flags.add("g")
            if static and uid not in self.ignore_next:
                # Cle ajoutee par script ou par un addon (keyframe_insert, ecriture
                # de f-curves) : Blender ne signale que l'objet anime, pas son
                # action. Sans cela, l'action n'etait retrouvee que par le
                # balayage de securite, plusieurs secondes plus tard.
                try:
                    ad = getattr(orig, "animation_data", None)
                    act = ad.action if ad is not None else None
                    act_uid = self.ptr2uid.get(act.as_pointer()) if act is not None else None
                except Exception:  # noqa: BLE001
                    act_uid = None
                if act_uid is not None:
                    self.dirty.setdefault(act_uid, set()).add("x")
        # Seule l'evaluation qui suit notre propre lecture est ignoree.
        self.ignore_next.clear()

    def mark_all_dirty(self):
        for uid in self.index:
            self.dirty.setdefault(uid, set()).update(("x", "g"))

    # ------------------------------------------------------------------
    # Objets en mode Edition / Sculpt / Pose... : lecture periodique
    # ------------------------------------------------------------------
    def objects_in_mode(self, now):
        if now - self.mode_scan_t > 0.25:
            self.mode_scan_t = now
            vl = compat.current_view_layer()
            res = []
            if vl is not None:
                try:
                    for o in vl.objects:
                        if o.mode != "OBJECT":
                            res.append(o)
                except Exception:
                    pass
            self.mode_objects = res
        out = []
        for o in self.mode_objects:
            try:
                o.mode  # noqa: B018 - verifie la validite
                out.append(o)
            except ReferenceError:
                continue
        return out

    def poll_modes(self, now, locked_by_me):
        for obj in self.objects_in_mode(now):
            ouid = ids.get_uid(obj)
            if ouid is None:
                continue
            data = obj.data
            duid = ids.get_uid(data) if data is not None else None
            size = len(data.vertices) if isinstance(data, bpy.types.Mesh) else 1000
            interval = min(3.0, max(self.edit_interval, size / 150000.0))
            locked_by_me.add(ouid)
            if duid:
                locked_by_me.add(duid)
            if duid and self.live.holding(duid, now):
                continue    # geste en cours : le flux temps reel s'en occupe
            last = self.edit_last.get(ouid, 0.0)
            if now - last < interval:
                continue
            # On ne relit que s'il y a eu une activite (ou par securite toutes les 5 s) :
            # relire un gros maillage a vide couterait du temps pour rien.
            active = ouid in self.mode_activity or (duid in self.mode_activity if duid else False)
            if not active and now - last < 5.0:
                continue
            self.mode_activity.discard(ouid)
            if duid:
                self.mode_activity.discard(duid)
            self.edit_last[ouid] = now
            if obj.mode == "EDIT":
                try:
                    obj.update_from_editmode()
                    self.ignore_next.add(ouid)
                    if duid:
                        self.ignore_next.add(duid)
                except Exception:
                    pass
            if duid:
                self.dirty.setdefault(duid, set()).update(("x", "g"))
            self.dirty.setdefault(ouid, set()).add("x")

    # ------------------------------------------------------------------
    # Collecte des changements locaux
    # ------------------------------------------------------------------
    def operator_label(self):
        try:
            ops = bpy.context.window_manager.operators
            if not len(ops):
                return None
            op = ops[-1]
            ptr = op.as_pointer()
        except Exception:
            return None
        if ptr != self.last_op_ptr:
            self.last_op_ptr = ptr
            try:
                return op.bl_label
            except Exception:
                return None
        return None

    def _with_blob(self, uid, attr, idb, fields, now, force=False):
        if not force and now - self.blob_last.get(uid, 0.0) < self.blob_interval:
            return False
        fields.update(serialize.write_blob(idb, attr, self))
        self.blob_last[uid] = now
        return True

    @staticmethod
    def animated_fields(idb):
        """Valeurs pilotees par l'animation ou des drivers : jamais diffusees.

        Sinon chaque changement de frame (ou lecture de la timeline) serait vu
        comme une modification et renvoye a tout le monde. Retourne (champs
        entierement pilotes, masques des parties pilotees d'un champ : os,
        proprietes, modificateurs...), voir animmask.
        """
        ad = getattr(idb, "animation_data", None)
        if ad is None:
            return (), None
        fields, masks = animmask.split(compat.animated_paths(ad))
        if len(ad.nla_tracks):
            fields.update(("location", "rotation_euler", "rotation_quaternion", "scale", "_pose"))
        return fields, masks

    def _frame_changed(self):
        sc = compat.current_scene()
        fr = (sc.name, sc.frame_current, round(sc.frame_subframe, 3)) if sc is not None else None
        changed = fr != getattr(self, "_last_frame", None)
        self._last_frame = fr
        return changed

    def collect(self, now=None, rr_budget=None, full=False):
        now = now or time.time()
        if self.need_scan or self.stale or now - self.last_scan > 0.5:
            self.scan()
        changes = []
        keep_dirty = {}
        frame_changed = self._frame_changed()
        if (self.deferred or self.deferred_creates) and now - getattr(self, "_retry_t", 0) > 0.25:
            self._retry_t = now
            self.retry_deferred()
        if self.wanted_names:
            self.fix_names()
        # 1. Creations
        new = [u for u in self.index if u not in self.known]
        new.sort(key=lambda u: ids.order_of(self.attr_of[u]))
        for uid in new:
            attr = self.attr_of[uid]
            idb = self.index[uid]
            try:
                f = serialize.read(attr, idb, self, deep=True)
                if ids.mode_of(attr) == "blob" or f.get("_mode") == "b":
                    self._with_blob(uid, attr, idb, f, now, force=True)
            except Exception as exc:  # noqa: BLE001
                self.log("creation %s: %s" % (getattr(idb, "name", uid), exc))
                continue
            changes.append({"u": uid, "t": attr, "k": "c", "f": f})
            self.tombstones.discard(uid)
        # 2. Mises a jour (sales + balayage tournant)
        cand = dict(self.dirty)
        self.dirty = {}
        # Balayage de securite : seulement quand rien d'autre n'attend, pour ne
        # jamais ralentir une manipulation en cours.
        if self.rr and (rr_budget is not None or not cand):
            budget = rr_budget if rr_budget is not None else max(4, len(self.rr) // 40)
            for _ in range(min(budget, len(self.rr))):
                if self.rr_pos >= len(self.rr):
                    self.rr_pos = 0
                uid = self.rr[self.rr_pos]
                self.rr_pos += 1
                cand.setdefault(uid, set()).add("r")
        # Les deplacements d'abord (latence), puis les objets (pose d'un rig pilote
        # par un addon...), puis le reste dans la limite du budget. Sans cela, les
        # materiaux, lumieres et maillages recalcules par des drivers passaient
        # avant le rig qui bouge et le retardaient.
        waiting = self.waiting

        def _prio(kv):
            uid, fl = kv
            w = waiting.get(uid)
            if w is not None and now - w > 0.2:
                return -1   # deja repousse : passe en premier, rien n'attend indefiniment
            if fl == {"t"}:
                return 0
            if "r" in fl:
                return 3
            return 1 if self.attr_of.get(uid) == "objects" else 2
        order = sorted(cand.items(), key=_prio)
        t_start = time.perf_counter()
        limit = None if (full or rr_budget is not None) else self.budget
        moving = {self.uid_ptr.get(u) for u, f in cand.items() if "t" in f or "x" in f}
        for uid, flags in order:
            if uid not in self.known or uid not in self.index:
                continue
            if self.live.holding(uid, now, full):
                if flags != {"r"}:
                    keep_dirty.setdefault(uid, set()).update(flags)
                continue
            attr = self.attr_of[uid]
            idb = self.index[uid]
            mode = ids.mode_of(attr)
            if flags == {"t"} and attr == "objects":
                try:
                    par = idb.parent
                    if par is not None and par.as_pointer() in moving:
                        continue    # suit son parent qui bouge : rien a envoyer
                except ReferenceError:
                    continue
                tdiff = self._fast_transform(uid, idb)
                if tdiff is None:
                    flags = {"x"}   # immobile et independant : autre reglage -> lecture complete
                else:
                    if tdiff:
                        changes.append({"u": uid, "t": attr, "k": "u", "f": tdiff})
                    continue
            if attr == "objects" and "x" in flags and limit is not None:
                # Enfant dont la geometrie est reevaluee parce que son parent bouge :
                # une relecture complete toutes les 0,5 s suffit.
                try:
                    par = idb.parent
                    if par is not None and par.as_pointer() in moving and                             now - self.obj_last.get(uid, 0.0) < 0.5:
                        keep_dirty.setdefault(uid, set()).update(flags)
                        continue
                except ReferenceError:
                    continue
                self.obj_last[uid] = now
            if limit is not None and time.perf_counter() - t_start > limit:
                if flags != {"r"}:
                    keep_dirty.setdefault(uid, set()).update(flags)
                    waiting.setdefault(uid, now)
                continue
            waiting.pop(uid, None)
            if attr == "meshes":
                size = len(idb.vertices)
                wait = max(0.15, size / 200000.0)
                if ("g" in flags or "x" in flags) and now - self.mesh_last.get(uid, 0.0) < wait:
                    keep_dirty[uid] = flags     # au plus une relecture complete par intervalle
                    continue
                # Sans "g", la geometrie, les cles de forme et les poids n'ont pas
                # change (ex. maillage reevalue par un driver) : lecture legere.
                deep = "g" in flags or ("r" in flags and size < 100000)
                if deep:
                    self.mesh_last[uid] = now
            elif mode == "blob":
                deep = True
                if now - self.blob_last.get(uid, 0.0) < self.blob_interval and "r" not in flags:
                    keep_dirty[uid] = flags
                    continue
            elif attr == "scenes":
                deep = "r" in flags
            else:
                deep = True
            try:
                cur = serialize.read(attr, idb, self, deep=deep)
            except ReferenceError:
                self.stale = True
                continue
            except Exception as exc:  # noqa: BLE001
                self.log("lecture %s: %s" % (attr, exc))
                continue
            kf = self.known[uid]["f"]
            for k in self.smoother.fields_of(uid):
                cur.pop(k, None)   # en train de glisser vers la valeur connue
            passive = serialize.PASSIVE_FIELDS
            masks = None
            if mode != "blob" and attr != "meshes":
                anim, masks = self.animated_fields(idb)
                if anim:
                    passive = passive | anim
            elif frame_changed and compat.has_animation(idb):
                # empreinte qui bouge a cause de l'animation, pas d'une edition
                passive = passive | {"fp"}
            diff = {}
            for k, v in cur.items():
                if kf.get(k) != v:
                    if k in passive or (masks and k in masks and
                                        animmask.equal(k, v, kf.get(k), masks[k])):
                        kf[k] = v  # tenu a jour sans etre diffuse
                    else:
                        diff[k] = v
            if not diff:
                continue
            try:
                if attr == "images" and "fsig" in diff and kf.get("fsig") is not None:
                    # Fichier de texture modifie sur le disque : on le recharge ici
                    # (sauf retouches non enregistrees) et on l'envoie a tous.
                    try:
                        if not idb.is_dirty:
                            idb.reload()
                    except Exception:  # noqa: BLE001
                        pass
                if (mode == "blob" and ("fp" in diff or "fsig" in diff)) or \
                        (cur.get("_mode") == "b" and ("fp" in diff or "_mode" in diff)):
                    if not self._with_blob(uid, attr, idb, diff, now):
                        keep_dirty[uid] = flags
                        continue
            except Exception as exc:  # noqa: BLE001
                self.log("blob %s: %s" % (idb.name, exc))
                continue
            changes.append({"u": uid, "t": attr, "k": "u", "f": diff})
        self.dirty.update(keep_dirty)
        # Pixels des textures peintes (non enregistrees)
        changes.extend(self._collect_pixels(now))
        # 3. Suppressions
        for uid in [u for u in self.known if u not in self.index]:
            k = self.known[uid]
            changes.append({"u": uid, "t": k["t"], "k": "d", "n": k["f"].get("name", "")})
        return changes

    def _fast_transform(self, uid, idb):
        """Compare la transformation seule. Retourne le diff (eventuellement
        vide), ou None si l'objet n'a pas bouge alors qu'il ne depend de rien :
        un autre reglage a change (ex. affichage), il faut une lecture complete."""
        try:
            cur = serialize.read_object_fast(idb)
        except ReferenceError:
            self.stale = True
            return {}
        kf = self.known[uid]["f"]
        for k in self.smoother.fields_of(uid):
            cur.pop(k, None)
        anim, _masks = self.animated_fields(idb)
        diff = {}
        for k, v in cur.items():
            if kf.get(k) != v:
                if k in anim:
                    kf[k] = v
                else:
                    diff[k] = v
        if diff:
            return diff
        try:
            dependent = idb.parent is not None or len(idb.constraints) > 0 \
                or idb.animation_data is not None
        except ReferenceError:
            return {}
        return {} if dependent else None

    def _collect_pixels(self, now):
        if now - self.px_t < 0.25:
            return []
        self.px_t = now
        out = []
        for uid, idb in list(self.index.items()):
            if self.attr_of.get(uid) != "images" or uid not in self.known:
                continue
            try:
                if not idb.is_dirty or not serialize.pixels_supported(idb):
                    continue
                w, h = idb.size
            except ReferenceError:
                continue
            st = self.px_state.setdefault(uid, [None, 0.0, 0.0])
            if now - st[2] < (0.4 if w * h <= 2048 * 2048 else 1.2):
                continue
            st[2] = now
            try:
                data, meta = serialize.image_pixels(idb)
            except Exception as exc:  # noqa: BLE001
                self.log("pixels %s: %s" % (idb.name, exc))
                continue
            key = payloads.sha(data)
            stable = key == st[0]
            st[0] = key
            if key == self.px_known.get(uid):
                continue
            # Pendant la peinture : au plus un envoi toutes les 1,5 s ; des que
            # l'image ne bouge plus, envoi immediat.
            if not stable and now - st[1] < 1.5:
                continue
            st[1] = now
            self.store.put(data, key=key)
            self.px_known[uid] = key
            meta["px"] = {"$p": key}
            out.append({"u": uid, "t": "images", "k": "u", "f": {"_px": meta}})
        return out

    def settle(self, uid):
        """Fin d'un glissement : l'etat reel devient l'etat connu (arrondis inclus)."""
        idb = self.resolve(uid)
        entry = self.known.get(uid)
        if idb is None or entry is None:
            return
        try:
            cur = serialize.read(entry["t"], idb, self, deep=False)
        except Exception:  # noqa: BLE001
            return
        for k in smooth.FIELDS:
            if k in cur:
                entry["f"][k] = cur[k]

    def mark_sent(self, changes):
        """Les changements envoyes deviennent l'etat connu."""
        for c in changes:
            uid = c["u"]
            if c["k"] == "d":
                self.known.pop(uid, None)
                self.tombstones.add(uid)
            else:
                entry = self.known.setdefault(uid, {"t": c["t"], "f": {}})
                entry["f"].update(c["f"])

    # ------------------------------------------------------------------
    # Application des changements distants
    # ------------------------------------------------------------------
    def accept(self, uid, attr, fields, created=False):
        idb = self.resolve(uid)
        if idb is None:
            return
        deep = created or any(serialize.is_deep_field(attr, k) for k in fields)
        try:
            if attr == "objects" and not created and fields and \
                    set(fields) <= serialize.FAST_OBJECT_SET:
                cur = serialize.read_object_fast(idb)
            else:
                cur = serialize.read(attr, idb, self, deep=deep)
        except Exception:
            cur = dict(fields)
        entry = self.known.get(uid)
        if entry is None or created:
            entry = {"t": attr, "f": cur}
            self.known[uid] = entry
        else:
            kf = entry["f"]
            for k in set(fields) | {"name"}:
                if k in cur:
                    kf[k] = cur[k]
        for k in ("blob", "file", "tiles"):
            if k in fields:
                entry["f"][k] = fields[k]
        px = fields.get("_px")
        if px:
            entry["f"]["_px"] = px
            try:
                self.px_known[uid] = px["px"]["$p"]
            except (KeyError, TypeError):
                pass
        want = fields.get("name")
        if want and not self.session.is_host and cur.get("name") and cur["name"] != want:
            # Nom pris localement (ex: deux joueurs creent "Cube" en meme temps) :
            # on l'appliquera des qu'il se libere, sans le rediffuser.
            self.wanted_names[uid] = [time.time(), want]
        self.dirty.pop(uid, None)

    def fix_names(self):
        now = time.time()
        for uid, (t, want) in list(self.wanted_names.items()):
            idb = self.resolve(uid)
            if idb is None or now - t > 60:
                self.wanted_names.pop(uid, None)
                continue
            if idb.name == want:
                self.wanted_names.pop(uid, None)
                continue
            coll = getattr(bpy.data, self.attr_of.get(uid, ""), None)
            if coll is not None and coll.get(want) is None:
                try:
                    idb.name = want
                except Exception:
                    pass
                k = self.known.get(uid)
                if k is not None:
                    k["f"]["name"] = idb.name
                if idb.name == want:
                    self.wanted_names.pop(uid, None)

    def apply_remote(self, changes):
        """Applique une liste de changements. Retourne la liste des erreurs."""
        errors = []
        creates = sorted([c for c in changes if c["k"] == "c"], key=lambda c: ids.order_of(c["t"]))
        updates = sorted([c for c in changes if c["k"] == "u"], key=lambda c: ids.order_of(c["t"]))
        deletes = sorted([c for c in changes if c["k"] == "d"], key=lambda c: -ids.order_of(c["t"]))
        applied = []
        glides = []
        rescan = bool(creates or deletes)
        for c in creates + updates:
            uid, attr, f = c["u"], c["t"], c.get("f", {})
            try:
                idb = self.resolve(uid)
                created = False
                if idb is None:
                    if c["k"] == "u":
                        errors.append((uid, "missing"))
                        continue
                    serialize.create(attr, uid, f, self)
                    created = True
                else:
                    targets = {}
                    if attr == "objects" and self.smoother.enabled and idb.mode == "OBJECT":
                        targets = {k: v for k, v in f.items() if k in smooth.FIELDS}
                    if targets:
                        taken = self.smoother.add(uid, idb, targets)
                        if taken:
                            glides.append((uid, {k: targets[k] for k in taken}))
                            f = {k: v for k, v in f.items() if k not in taken}
                    serialize.apply_fields(attr, uid, idb, f, self)
                self.tombstones.discard(uid)
                applied.append((uid, attr, f, created))
                if "blob" in f or "_vg" in f or "data" in f:
                    rescan = True
            except serialize.Unresolved:
                self.deferred_creates.append([time.time(), c])
            except serialize.Locked:
                errors.append((uid, "locked"))
            except serialize.MissingPayload:
                errors.append((uid, "payload"))
            except Exception as exc:  # noqa: BLE001
                self.log("application %s %s: %s" % (attr, f.get("name", uid), exc))
                errors.append((uid, "error"))
        for c in deletes:
            uid = c["u"]
            idb = self.resolve(uid)
            if idb is not None:
                try:
                    if getattr(idb, "is_editmode", False) or \
                            (isinstance(idb, bpy.types.Object) and idb.mode != "OBJECT"):
                        errors.append((uid, "locked"))
                        continue
                except ReferenceError:
                    pass
                serialize.delete(c["t"], idb, self)
            self.forget(uid)
        if rescan:
            self.scan()
        for uid, attr, f, created in applied:
            self.accept(uid, attr, f, created)
            self.live.note_remote(uid)
        for uid, targets in glides:
            entry = self.known.get(uid)
            if entry is not None:
                entry["f"].update(targets)   # valeur finale, atteinte en fin de glissement
        self.retry_deferred()
        return errors

    def forget(self, uid):
        self.known.pop(uid, None)
        idb = self.index.pop(uid, None)
        self.attr_of.pop(uid, None)
        ptr = self.uid_ptr.pop(uid, None)
        if ptr is not None:
            self.ptr2uid.pop(ptr, None)
        self.tombstones.add(uid)
        self.dirty.pop(uid, None)
        self.waiting.pop(uid, None)
        return idb

    def retry_deferred(self):
        now = time.time()
        if self.deferred_creates:
            items, self.deferred_creates = self.deferred_creates, []
            for t, c in items:
                try:
                    if self.resolve(c["u"]) is None:
                        serialize.create(c["t"], c["u"], c["f"], self)
                    else:
                        serialize.apply_fields(c["t"], c["u"], self.resolve(c["u"]), c["f"], self)
                    self.accept(c["u"], c["t"], c["f"], created=True)
                except serialize.Unresolved:
                    if now - t < 60:
                        self.deferred_creates.append([t, c])
                except Exception as exc:  # noqa: BLE001
                    self.log("creation differee: %s" % exc)
        if not self.deferred:
            return
        items, self.deferred = self.deferred, []
        for t, uid, attr, k, v in items:
            idb = self.resolve(uid)
            if idb is None:
                if now - t < 60:
                    self.deferred.append([t, uid, attr, k, v])
                continue
            try:
                serialize.apply_field(attr, idb, k, v, self)
                self.accept(uid, attr, {k: v})
            except serialize.Unresolved:
                if now - t < 60:
                    self.deferred.append([t, uid, attr, k, v])
            except Exception as exc:  # noqa: BLE001
                self.log("champ differe %s: %s" % (k, exc))

    # ------------------------------------------------------------------
    # Reconciliation : remettre l'etat local sur l'etat connu (autorite)
    # ------------------------------------------------------------------
    def restore_known(self, uids=None, keep=()):
        """Annule les divergences locales (ex: Ctrl+Z natif en session)."""
        self.scan()
        restored = 0
        touched = []
        targets = set(uids) if uids is not None else (set(self.known) | set(self.index))
        for uid in sorted(targets, key=lambda u: ids.order_of(self.attr_of.get(u) or
                                                              self.known.get(u, {}).get("t", ""))):
            if uid in keep:
                continue
            k = self.known.get(uid)
            idb = self.index.get(uid)
            try:
                if k is None and idb is not None:
                    serialize.delete(self.attr_of[uid], idb, self)
                    self.forget(uid)
                    self.tombstones.discard(uid)
                    restored += 1
                elif k is not None and idb is None:
                    serialize.create(k["t"], uid, k["f"], self)
                    touched.append(uid)
                    restored += 1
                elif k is not None:
                    cur = serialize.read(k["t"], idb, self, deep=True)
                    diff = {f: v for f, v in k["f"].items() if f in cur and cur[f] != v}
                    if "fp" in diff and "blob" in k["f"]:
                        diff["blob"] = k["f"]["blob"]
                    elif "fp" in diff:
                        diff.pop("fp")
                    diff.pop("_mode", None)
                    if diff:
                        serialize.apply_fields(k["t"], uid, idb, diff, self)
                        touched.append(uid)
                        restored += 1
            except Exception as exc:  # noqa: BLE001
                self.log("restauration %s: %s" % (uid, exc))
        self.scan()
        for uid in touched:
            if uid in self.index and uid in self.known:
                self.accept(uid, self.known[uid]["t"], self.known[uid]["f"])
        self.dirty.clear()
        return restored
