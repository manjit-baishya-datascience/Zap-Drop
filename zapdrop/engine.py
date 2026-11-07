"""Transfer engine.

Every transfer is executed by the RECEIVING PC, which pulls data from the sender:
  * pull     - you browse another PC's shares and download.
  * push_in  - another PC pushed files to you; you pull them from its temporary "offer".
  * push_out - you pushed files to another PC; this side only mirrors its progress.

Big files are split into 16 MiB chunks fetched by several workers in parallel; small
files are bundled into batches. Every chunk/batch carries an xxh3-128 hash that is
checked on arrival. Finished chunks are recorded in SQLite, so an interrupted transfer
resumes where it stopped.
"""

import math
import os
import queue
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path

import httpx
import xxhash

from .client import PeerClient, PeerError
from .fsutil import PathError, VirtualSource, free_name, sanitize_rel

MiB = 1024 * 1024
CHUNK = 16 * MiB
SMALL = 4 * MiB          # files up to this size are sent in batches
BATCH_BYTES = 32 * MiB
BATCH_FILES = 1000
WORKERS = 4
PART = ".zdpart"
RETRY_DELAYS = [1, 2, 4, 8, 15, 30]

PENDING, DONE, SKIPPED, FAILED = 0, 1, 2, 3
FINAL_STATES = ("done", "cancelled")
ACTIVE_STATES = ("queued", "running")


def hash_hex(b: bytes) -> str:
    return xxhash.xxh3_128_hexdigest(b)


class Fatal(Exception):
    """Stops the job; it can be resumed later."""


class HashMismatch(Exception):
    pass


class FEntry:
    __slots__ = ("idx", "src", "rel", "size", "mtime", "action", "status", "chunks", "final_rel", "error")

    def __init__(self, idx, src, rel, size, mtime, action="copy", status=PENDING, chunks=None, final_rel=""):
        self.idx, self.src, self.rel, self.size, self.mtime = idx, src, rel, int(size), float(mtime)
        self.action, self.status, self.final_rel = action, status, final_rel or ""
        self.chunks: set[int] = set(chunks or ())
        self.error = ""

    @property
    def nchunks(self) -> int:
        return max(1, math.ceil(self.size / CHUNK))

    def row(self):
        return (self.action, self.status, ",".join(map(str, sorted(self.chunks))), self.final_rel, self.idx)


class PartFile:
    def __init__(self, path: Path, resume: bool):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.f = open(path, "r+b" if resume and path.exists() else "w+b")
        self.lock = threading.Lock()

    def write_at(self, offset: int, data: bytes):
        with self.lock:
            self.f.seek(offset)
            self.f.write(data)

    def sync(self):
        with self.lock:
            if not self.f.closed:
                self.f.flush()
                os.fsync(self.f.fileno())

    def close(self):
        with self.lock:
            if not self.f.closed:
                self.f.close()


class Job:
    def __init__(self, meta: dict):
        self.meta = meta
        self.files: list[FEntry] = []
        self.lock = threading.RLock()
        self.done_bytes = 0
        self.done_files = 0
        self.failed_files = 0
        self.speed = 0.0
        self._last_bytes = 0
        self.current = ""
        self.pause_req = False
        self.cancel_req = False
        self.fatal = ""
        self.dirty: set[int] = set()
        self.parts: dict[int, PartFile] = {}
        self.conflicts: list[dict] = []
        self.failures: list[str] = []
        self.mirror: dict = {}  # push_out: last status reported by the receiver

    @property
    def id(self):
        return self.meta["id"]

    @property
    def state(self):
        return self.meta["state"]

    @state.setter
    def state(self, v):
        self.meta["state"] = v

    def recount(self):
        self.done_bytes = self.done_files = self.failed_files = 0
        for f in self.files:
            if f.status == DONE:
                self.done_bytes += f.size
                self.done_files += 1
            elif f.status == FAILED:
                self.failed_files += 1
            elif f.status == PENDING and f.chunks:
                self.done_bytes += sum(min(CHUNK, f.size - c * CHUNK) for c in f.chunks)
        self._last_bytes = self.done_bytes

    def to_dict(self) -> dict:
        m = self.meta
        d = {k: m.get(k) for k in ("id", "kind", "peer_id", "peer_alias", "label", "state", "error", "dest",
                                   "total_bytes", "total_files", "created", "started", "finished", "elapsed")}
        if m["kind"] == "push_out":
            mi = self.mirror
            d.update(done_bytes=mi.get("done_bytes", 0), done_files=mi.get("done_files", 0),
                     failed_files=mi.get("failed_files", 0), speed=mi.get("speed", 0),
                     eta=mi.get("eta"), current=mi.get("current", ""), note=m.get("note", ""),
                     failures=mi.get("failures", []))
            if mi.get("total_bytes") is not None:
                d.update(total_bytes=mi["total_bytes"], total_files=mi["total_files"])
            return d
        remaining = max(0, (m.get("total_bytes") or 0) - self.done_bytes)
        d.update(done_bytes=self.done_bytes, done_files=self.done_files, failed_files=self.failed_files,
                 speed=round(self.speed) if self.state == "running" else 0,
                 eta=(remaining / self.speed) if self.state == "running" and self.speed > 1 else None,
                 current=self.current if self.state == "running" else "", failures=self.failures[-50:],
                 note=m.get("note", ""))
        return d


class Engine:
    def __init__(self, db, cfg, peers, fingerprint: str):
        self.db, self.cfg, self.peers, self.fingerprint = db, cfg, peers, fingerprint
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.jobs: dict[str, Job] = {}
        self.running: Job | None = None
        self._stop = threading.Event()
        for meta in db.load_jobs():
            job = Job(meta)
            if meta["kind"] != "push_out":
                job.files = [FEntry(i, s, r, sz, mt, a, st, [int(x) for x in ch.split(",") if x] if ch else [], fr)
                             for (i, s, r, sz, mt, a, st, ch, fr) in db.load_files(meta["id"])]
                job.recount()
                if job.state == "running":
                    job.state = "paused"
                    job.meta["note"] = "Interrupted when Zap Drop closed. Press Resume to continue."
            job.mirror = meta.get("mirror", {})
            self.jobs[job.id] = job
            db.save_job(job.meta)

    def start(self):
        threading.Thread(target=self._runner, name="engine-runner", daemon=True).start()
        threading.Thread(target=self._ticker, name="engine-ticker", daemon=True).start()
        threading.Thread(target=self._push_out_poller, name="engine-pushout", daemon=True).start()

    def stop(self):
        self._stop.set()
        with self.lock:
            for j in self.jobs.values():
                if j.state == "running":
                    j.pause_req = True
            self.cv.notify_all()
        self._flush_all()

    # ------------------------------------------------------------------ queries
    def list(self) -> list[dict]:
        with self.lock:
            return [j.to_dict() for j in sorted(self.jobs.values(), key=lambda j: j.meta["created"], reverse=True)
                    if j.state != "draft"]

    def get(self, job_id: str) -> Job:
        j = self.jobs.get(job_id)
        if not j:
            raise KeyError("Unknown transfer")
        return j

    # ------------------------------------------------------------------ creation
    def _new_job(self, kind: str, manifest: dict, **meta) -> Job:
        now = time.time()
        m = {"id": uuid.uuid4().hex, "kind": kind, "state": "draft", "created": now, "started": None,
             "finished": None, "elapsed": 0.0, "error": "", "note": "", "dirs": [], **meta}
        job = Job(m)
        if kind != "push_out":
            dirs = []
            for d in manifest.get("dirs", []):
                try:
                    dirs.append("/".join(sanitize_rel(d)))
                except PathError:
                    pass
            m["dirs"] = dirs
            for i, f in enumerate(manifest.get("files", [])):
                try:
                    rel = "/".join(sanitize_rel(f["rel"]))
                except PathError:
                    continue
                job.files.append(FEntry(len(job.files), f["src"], rel, f["size"], f["mtime"]))
            m["total_bytes"] = sum(f.size for f in job.files)
            m["total_files"] = len(job.files)
            job.conflicts = self._find_conflicts(job)
        with self.lock:
            self.jobs[job.id] = job
        return job

    def _target(self, job: Job, rel: str) -> Path:
        return Path(job.meta["dest"], *rel.split("/"))

    def _find_conflicts(self, job: Job) -> list[dict]:
        out = []
        for f in job.files:
            t = self._target(job, f.rel)
            try:
                st = t.stat()
            except OSError:
                continue
            out.append({"idx": f.idx, "rel": f.rel, "is_dir": t.is_dir(), "local_size": st.st_size,
                        "local_mtime": st.st_mtime, "remote_size": f.size, "remote_mtime": f.mtime})
        return out

    @staticmethod
    def _label(names: list[str]) -> str:
        names = [n for n in names if n]
        if not names:
            return "Files"
        if len(names) <= 2:
            return ", ".join(names)
        return f"{names[0]}, {names[1]} +{len(names) - 2} more"

    def create_pull(self, peer, vpaths: list[str], dest: str) -> Job:
        dest = os.path.abspath(dest)
        Path(dest).mkdir(parents=True, exist_ok=True)
        try:
            with self.peers.client(peer) as c:
                man = c.post_json("/peer/manifest", {"paths": vpaths}, timeout=900)
        except PeerError as e:
            if e.status == 401:
                self.peers.mark_unauthorized(peer.id, "Session expired, connect again")
            raise
        job = self._new_job("pull", man, peer_id=peer.id, peer_alias=peer.alias, dest=dest,
                            label=self._label([v.rstrip("/").split("/")[-1] for v in vpaths]))
        job.failures = man.get("errors", [])[:50]
        return job

    def create_push_in(self, body: dict, host: str, owner: str) -> Job:
        dest = self.cfg.get("receive_dir")
        Path(dest).mkdir(parents=True, exist_ok=True)
        return self._new_job(
            "push_in", {"files": body.get("files", []), "dirs": body.get("dirs", [])},
            peer_id=body.get("sender_id"), peer_alias=body.get("sender_alias", "?"), host=host,
            port=int(body["port"]), tls_port=int(body["tls_port"]), use_tls=bool(body.get("use_tls")),
            fingerprint=body.get("fingerprint"), token=body["offer_token"], owner=owner,
            dest=os.path.abspath(dest), label=body.get("label") or "Files")

    def create_push_out(self, peer, paths: list[str]) -> Job:
        items: dict[str, str] = {}
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                raise PathError(f"Not found: {p}")
            name = os.path.basename(p.rstrip("\\/")) or p[0]
            base, n = name, 1
            while name in items:
                n += 1
                name = f"{base} ({n})"
            items[name] = p
        src = VirtualSource(items)
        man = src.manifest(list(items.keys()))
        offer_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self.db.save_offer(offer_id, token, items)
        label = self._label(list(items.keys()))
        body = {"offer_id": offer_id, "offer_token": token, "port": self.cfg.port, "tls_port": self.cfg.tls_port,
                "use_tls": self.cfg.get("mode") == "pin_tls", "fingerprint": self.fingerprint,
                "sender_id": self.cfg.get("id"), "sender_alias": self.cfg.get("alias"), "label": label,
                "files": man["files"], "dirs": man["dirs"]}
        try:
            with self.peers.client(peer) as c:
                res = c.post_json("/peer/push/prepare", body, timeout=900)
        except PeerError as e:
            self.db.delete_offer(offer_id)
            if e.status == 401:
                self.peers.mark_unauthorized(peer.id, "Session expired, connect again")
            raise
        job = self._new_job("push_out", {}, peer_id=peer.id, peer_alias=peer.alias, label=label,
                            remote_id=res["job_id"], offer_id=offer_id, dest=res.get("dest", ""),
                            total_bytes=man["total_bytes"], total_files=man["total_files"])
        job.conflicts = res.get("conflicts", [])
        job.failures = man.get("errors", [])[:50]
        return job

    # ------------------------------------------------------------------ control
    def start_job(self, job_id: str, default_action: str = "rename", per_file: dict | None = None):
        job = self.get(job_id)
        if job.state != "draft":
            raise ValueError("Transfer already started")
        per_file = {int(k): v for k, v in (per_file or {}).items()}
        if default_action not in ("overwrite", "skip", "rename"):
            default_action = "rename"
        if job.meta["kind"] == "push_out":
            peer = self.peers.get(job.meta["peer_id"])
            if not peer:
                raise PeerError(404, "The other PC is no longer available")
            with self.peers.client(peer) as c:
                c.post_json("/peer/push/start", {"job_id": job.meta["remote_id"], "default": default_action,
                                                 "per_file": {str(k): v for k, v in per_file.items()}})
            job.state = "queued"
            self.db.save_job(job.meta)
            return job
        for c in job.conflicts:
            f = job.files[c["idx"]]
            act = per_file.get(f.idx, default_action)
            if c["is_dir"] and act == "overwrite":
                act = "rename"  # never replace a folder with a file
            f.action = act
            if act == "skip":
                f.status = SKIPPED
        job.conflicts = []
        live = [f for f in job.files if f.status != SKIPPED]
        job.meta["total_bytes"] = sum(f.size for f in live)
        job.meta["total_files"] = len(live)
        job.state = "queued"
        self.db.insert_files(job.id, [(f.idx, f.src, f.rel, f.size, f.mtime, f.action, f.status,
                                       ",".join(map(str, sorted(f.chunks))), f.final_rel) for f in job.files])
        self.db.save_job(job.meta)
        job.recount()
        with self.lock:
            self.cv.notify_all()
        return job

    def discard_draft(self, job_id: str):
        with self.lock:
            j = self.jobs.get(job_id)
            if j and j.state == "draft":
                del self.jobs[job_id]
                if j.meta["kind"] == "push_out":
                    self.db.delete_offer(j.meta.get("offer_id", ""))

    def control(self, job_id: str, action: str):
        job = self.get(job_id)
        if job.meta["kind"] == "push_out":
            return self._control_push_out(job, action)
        with self.lock:
            if action == "pause":
                if job.state == "running":
                    job.pause_req = True
                elif job.state == "queued":
                    job.state = "paused"
            elif action == "resume":
                if job.state in ("paused", "error"):
                    job.state, job.meta["error"], job.meta["note"] = "queued", "", ""
                    job.pause_req = job.cancel_req = False
                    self.cv.notify_all()
            elif action == "cancel":
                if job.state == "running":
                    job.cancel_req = True
                elif job.state not in FINAL_STATES:
                    self._cleanup_parts(job)
                    self._finish(job, "cancelled")
            elif action == "remove":
                if job.state == "running":
                    raise ValueError("Pause or cancel the transfer first")
                if job.state not in FINAL_STATES and job.state != "draft":
                    self._cleanup_parts(job)
                    self._finish(job, "cancelled")
                self.jobs.pop(job_id, None)
                self.db.delete_job(job_id)
                return None
            self.db.save_job(job.meta)
        return job

    def _control_push_out(self, job: Job, action: str):
        if action == "remove":
            with self.lock:
                if job.state not in FINAL_STATES:
                    try:
                        self._control_remote(job, "cancel")
                    except Exception:
                        pass
                self.jobs.pop(job.id, None)
                self.db.delete_job(job.id)
                self.db.delete_offer(job.meta.get("offer_id", ""))
            return None
        self._control_remote(job, action)
        return job

    def _control_remote(self, job: Job, action: str):
        peer = self.peers.get(job.meta["peer_id"])
        if not peer:
            raise PeerError(404, "The other PC is not available")
        with self.peers.client(peer) as c:
            c.post_json(f"/peer/push/control/{job.meta['remote_id']}", {"action": action})

    # ------------------------------------------------------------------ running
    def _runner(self):
        while not self._stop.is_set():
            with self.lock:
                nxt = None
                while not self._stop.is_set():
                    queued = [j for j in self.jobs.values()
                              if j.state == "queued" and j.meta["kind"] != "push_out"]
                    if queued:
                        nxt = min(queued, key=lambda j: j.meta["created"])
                        break
                    self.cv.wait(1.0)
                if nxt is None:
                    return
                nxt.state = "running"
                nxt.pause_req = nxt.cancel_req = False
                nxt.fatal = ""
                nxt.meta["error"] = nxt.meta["note"] = ""
                nxt.meta["started"] = nxt.meta["started"] or time.time()
                self.running = nxt
                self.db.save_job(nxt.meta)
            try:
                self._run(nxt)
            finally:
                self.running = None

    def _client_for(self, job: Job) -> PeerClient:
        m = job.meta
        if m["kind"] == "pull":
            peer = self.peers.get(m["peer_id"])
            if not peer:
                raise Fatal(f"{m['peer_alias']} is not visible on the network right now.")
            if m["peer_id"] not in self.peers.tokens:
                raise Fatal(f"Not connected to {m['peer_alias']}. Connect to it, then press Resume.")
            return self.peers.client(peer, connections=WORKERS + 2)
        return PeerClient(m["host"], m["port"], m["tls_port"], m["use_tls"], m.get("fingerprint"),
                          m["token"], connections=WORKERS + 2)

    def _run(self, job: Job):
        t0 = time.time()
        job.speed = 0.0
        job.recount()
        try:
            dest = Path(job.meta["dest"])
            dest.mkdir(parents=True, exist_ok=True)
            units = self._plan(job)
            need = sum(f.size for f in job.files if f.status == PENDING) - sum(
                min(CHUNK, f.size - c * CHUNK) for f in job.files if f.status == PENDING for c in f.chunks)
            free = shutil.disk_usage(dest).free
            if need > free:
                raise Fatal(f"Not enough free space on {dest.anchor}: needs {need / 1e9:.1f} GB, "
                            f"{free / 1e9:.1f} GB free.")
            for d in job.meta.get("dirs", []):
                try:
                    (dest / Path(*d.split("/"))).mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            client = self._client_for(job)
            try:
                q: queue.Queue = queue.Queue()
                for u in units:
                    q.put(u)
                workers = [threading.Thread(target=self._worker, args=(job, client, q), daemon=True)
                           for _ in range(min(WORKERS, max(1, len(units))))]
                for w in workers:
                    w.start()
                for w in workers:
                    w.join()
            finally:
                client.close()
                with job.lock:
                    for pf in job.parts.values():
                        pf.close()
                    job.parts.clear()
                self._flush(job)
            if job.cancel_req:
                self._cleanup_parts(job)
                job.meta["elapsed"] += time.time() - t0
                self._finish(job, "cancelled")
                return
            if job.fatal:
                raise Fatal(job.fatal)
            if job.pause_req:
                job.state = "paused"
            else:
                job.meta["elapsed"] += time.time() - t0
                self._finish(job, "done")
                return
        except Fatal as e:
            job.state, job.meta["error"] = "error", str(e)
        except Exception as e:  # unexpected local problem
            job.state, job.meta["error"] = "error", f"{type(e).__name__}: {e}"
        job.meta["elapsed"] += time.time() - t0
        self.db.save_job(job.meta)

    def _finish(self, job: Job, state: str):
        job.state = state
        job.meta["finished"] = time.time()
        self.db.save_job(job.meta)
        m = job.meta
        if m["kind"] == "push_out":
            mi = job.mirror
            self.db.add_history(direction="sent", peer=m["peer_alias"], label=m["label"],
                                files=mi.get("done_files", 0), bytes=mi.get("done_bytes", 0),
                                duration=mi.get("elapsed", 0), status=state if not mi.get("failed_files")
                                else f"{state} ({mi['failed_files']} failed)", dest=m.get("dest", ""))
            self.db.delete_offer(m.get("offer_id", ""))
            return
        status = state if not job.failed_files else f"{state} ({job.failed_files} failed)"
        self.db.add_history(direction="received", peer=m["peer_alias"], label=m["label"],
                            files=job.done_files, bytes=job.done_bytes, duration=m.get("elapsed", 0),
                            status=status, dest=m["dest"], error="; ".join(job.failures[:5]))

    def _plan(self, job: Job) -> list[tuple]:
        units, batch, bbytes = [], [], 0
        for f in job.files:
            if f.status != PENDING:
                continue
            if f.size <= SMALL:
                batch.append(f.idx)
                bbytes += f.size
                if bbytes >= BATCH_BYTES or len(batch) >= BATCH_FILES:
                    units.append(("batch", batch))
                    batch, bbytes = [], 0
                continue
            if f.chunks:
                part = self._part_path(job, f)
                have = part.stat().st_size if part.exists() else -1
                valid = {c for c in f.chunks if c * CHUNK + min(CHUNK, f.size - c * CHUNK) <= have}
                if valid != f.chunks:
                    f.chunks = valid
                    job.dirty.add(f.idx)
            for c in range(f.nchunks):
                if c not in f.chunks:
                    units.append(("chunk", f.idx, c))
        if batch:
            units.append(("batch", batch))
        job.recount()
        return units

    def _stopping(self, job: Job) -> bool:
        return job.pause_req or job.cancel_req or bool(job.fatal) or self._stop.is_set()

    def _worker(self, job: Job, client: PeerClient, q: queue.Queue):
        while not self._stopping(job):
            try:
                unit = q.get_nowait()
            except queue.Empty:
                return
            self._do_unit(job, client, unit)

    def _unit_files(self, job: Job, unit) -> list[FEntry]:
        return [job.files[unit[1]]] if unit[0] == "chunk" else [job.files[i] for i in unit[1]]

    def _do_unit(self, job: Job, client: PeerClient, unit):
        err = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            if self._stopping(job):
                return
            try:
                if unit[0] == "chunk":
                    self._fetch_chunk(job, client, unit[1], unit[2])
                else:
                    self._fetch_batch(job, client, unit[1])
                return
            except PeerError as e:
                if e.status in (401, 403, 495):
                    if e.status == 401 and job.meta["kind"] == "pull":
                        self.peers.mark_unauthorized(job.meta["peer_id"], "Session expired, connect again")
                    job.fatal = f"{job.meta['peer_alias']} refused access: {e.message}"
                    return
                if e.status in (400, 404, 409, 410):
                    for f in self._unit_files(job, unit):
                        self._fail_file(job, f, e.message)
                    return
                err = e.message
            except (httpx.TransportError, HashMismatch) as e:
                err = str(e) or type(e).__name__
            except OSError as e:
                if getattr(e, "errno", None) == 28 or getattr(e, "winerror", None) in (39, 112):
                    job.fatal = "The destination disk is full."
                    return
                for f in self._unit_files(job, unit):
                    self._fail_file(job, f, f"Could not write: {e.strerror or e}")
                return
            if attempt < len(RETRY_DELAYS):
                job.meta["note"] = f"Connection problem, retrying... ({err})"
                deadline = time.time() + RETRY_DELAYS[attempt]
                while time.time() < deadline and not self._stopping(job):
                    time.sleep(0.2)
        job.fatal = f"Lost connection to {job.meta['peer_alias']}: {err}"

    def _part_path(self, job: Job, f: FEntry) -> Path:
        t = self._target(job, f.rel)
        return t.with_name(t.name + PART)

    def _fail_file(self, job: Job, f: FEntry, msg: str):
        with job.lock:
            if f.status != PENDING:
                return
            f.status = FAILED
            f.chunks.clear()
            job.failed_files += 1
            job.failures.append(f"{f.rel}: {msg}")
            job.dirty.add(f.idx)
            pf = job.parts.pop(f.idx, None)
        if pf:
            pf.close()
        try:
            self._part_path(job, f).unlink(missing_ok=True)
        except OSError:
            pass
        job.recount()

    def _fetch_chunk(self, job: Job, client: PeerClient, fidx: int, cidx: int):
        f = job.files[fidx]
        off = cidx * CHUNK
        ln = min(CHUNK, f.size - off)
        data, h = client.get_bytes("/peer/chunk", {"path": f.src, "offset": off, "length": ln,
                                                   "size": f.size, "mtime": f.mtime})
        if len(data) != ln or hash_hex(data) != h.get("x-zapdrop-hash"):
            raise HashMismatch("Checksum mismatch, re-downloading chunk")
        with job.lock:
            if f.status != PENDING:
                return
            pf = job.parts.get(fidx)
            if pf is None:
                pf = PartFile(self._part_path(job, f), resume=bool(f.chunks))
                job.parts[fidx] = pf
        pf.write_at(off, data)
        with job.lock:
            if cidx in f.chunks:
                return
            f.chunks.add(cidx)
            job.done_bytes += ln
            job.dirty.add(fidx)
            job.current = f.rel
            complete = len(f.chunks) == f.nchunks
            if complete:
                job.parts.pop(fidx, None)
        if complete:
            pf.close()
            self._finalize(job, f, self._part_path(job, f))

    def _fetch_batch(self, job: Job, client: PeerClient, idxs: list[int]):
        files = [job.files[i] for i in idxs if job.files[i].status == PENDING]
        if not files:
            return
        data, h = client.post_bytes("/peer/batch", {"files": [{"path": f.src, "size": f.size, "mtime": f.mtime}
                                                              for f in files]})
        if hash_hex(data) != h.get("x-zapdrop-hash"):
            raise HashMismatch("Checksum mismatch, re-downloading batch")
        sizes = [int(x) for x in h.get("x-zapdrop-sizes", "").split(",") if x]
        if len(sizes) != len(files) or sum(s for s in sizes if s > 0) != len(data):
            raise HashMismatch("Batch size mismatch")
        off = 0
        for f, sz in zip(files, sizes):
            if sz < 0:
                self._fail_file(job, f, "Changed, missing or unreadable on the other PC")
                continue
            part = self._part_path(job, f)
            part.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as fh:
                fh.write(data[off:off + sz])
            off += sz
            with job.lock:
                job.done_bytes += sz
                job.current = f.rel
            self._finalize(job, f, part)

    def _finalize(self, job: Job, f: FEntry, part: Path):
        try:
            os.utime(part, (f.mtime, f.mtime))
        except OSError:
            pass
        target = self._target(job, f.rel)
        if f.action == "overwrite" and not target.is_dir():
            os.replace(part, target)
        else:
            if target.exists():
                target = free_name(target)
            os.rename(part, target)
        with job.lock:
            f.status = DONE
            f.final_rel = os.path.relpath(target, job.meta["dest"]).replace("\\", "/")
            job.done_files += 1
            job.dirty.add(f.idx)

    def _cleanup_parts(self, job: Job):
        for f in job.files:
            if f.status == PENDING:
                try:
                    self._part_path(job, f).unlink(missing_ok=True)
                except OSError:
                    pass
                f.chunks.clear()
                job.dirty.add(f.idx)
        self._flush(job)

    # ------------------------------------------------------------------ bookkeeping
    def _flush(self, job: Job):
        with job.lock:
            dirty, job.dirty = job.dirty, set()
            parts = list(job.parts.values())
            rows = [job.files[i].row() for i in dirty]
        for pf in parts:  # make sure data is on disk before recording chunks as done
            try:
                pf.sync()
            except (OSError, ValueError):
                pass
        self.db.update_files(job.id, rows)

    def _flush_all(self):
        for j in list(self.jobs.values()):
            if j.dirty:
                self._flush(j)

    def _ticker(self):
        tick = 0
        while not self._stop.wait(1.0):
            tick += 1
            with self.lock:
                jobs = list(self.jobs.values())
            now = time.time()
            for j in jobs:
                if j.state == "running" and j.meta["kind"] != "push_out":
                    inst = j.done_bytes - j._last_bytes
                    j._last_bytes = j.done_bytes
                    j.speed = inst if j.speed == 0 else 0.6 * j.speed + 0.4 * inst
                    if j.meta.get("note", "").startswith("Connection problem") and inst > 0:
                        j.meta["note"] = ""
                elif j.state == "draft" and now - j.meta["created"] > 1800:
                    self.discard_draft(j.id)
            if tick % 3 == 0:
                self._flush_all()

    def _push_out_poller(self):
        while not self._stop.wait(1.0):
            with self.lock:
                jobs = [j for j in self.jobs.values()
                        if j.meta["kind"] == "push_out" and j.state not in FINAL_STATES + ("draft",)]
            for j in jobs:
                peer = self.peers.get(j.meta["peer_id"])
                if not peer:
                    j.meta["note"] = f"Waiting for {j.meta['peer_alias']} to come back online"
                    continue
                try:
                    with self.peers.client(peer) as c:
                        st = c.get_json(f"/peer/push/status/{j.meta['remote_id']}")
                except PeerError as e:
                    if e.status == 404:
                        j.mirror.setdefault("done_bytes", 0)
                        self._finish(j, "cancelled")
                        j.meta["error"] = "Removed on the other PC"
                        self.db.save_job(j.meta)
                    else:
                        j.meta["note"] = f"{j.meta['peer_alias']}: {e.message}"
                    continue
                except Exception:
                    j.meta["note"] = f"Waiting for {j.meta['peer_alias']} to come back online"
                    continue
                j.mirror = st
                j.meta["note"] = st.get("note", "")
                j.meta["error"] = st.get("error", "")
                j.meta["mirror"] = st
                if st["state"] in FINAL_STATES:
                    self._finish(j, st["state"])
                elif st["state"] != j.state:
                    j.state = st["state"]
                    self.db.save_job(j.meta)
