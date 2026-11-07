"""HTTP server: the peer API (PC <-> PC) and the UI API (browser <-> this PC)."""

import os
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, VERSION
from .client import PeerError
from .config import MODES
from .discovery import local_ipv4s
from .engine import BATCH_FILES, hash_hex
from .fsutil import PathError, VirtualSource, list_dir, list_drives
from .security import check_secret, hash_secret

WEB_DIR = Path(__file__).parent / "web"
MAX_CHUNK = 64 * 1024 * 1024
APPROVAL_TTL = 120
ADMIN_SESSION_TTL = 12 * 3600


class ServeStats:
    """Live upload speed per device that is downloading from this PC."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s: dict[str, dict] = {}

    def add(self, who: str, n: int):
        with self._lock:
            s = self._s.setdefault(who, {"bytes": 0, "last": 0, "speed": 0.0, "seen": 0})
            s["bytes"] += n
            s["seen"] = time.time()

    def tick(self):
        with self._lock:
            now = time.time()
            for k in list(self._s):
                s = self._s[k]
                inst = s["bytes"] - s["last"]
                s["last"] = s["bytes"]
                s["speed"] = inst if s["speed"] == 0 else 0.6 * s["speed"] + 0.4 * inst
                if now - s["seen"] > 30:
                    del self._s[k]

    def list(self):
        with self._lock:
            now = time.time()
            return [{"alias": k, "bytes": v["bytes"], "speed": round(v["speed"]) if now - v["seen"] < 3 else 0}
                    for k, v in self._s.items()]


class Context:
    def __init__(self, cfg, db, tokens, limiter, peers, engine, discovery, fingerprint):
        self.cfg, self.db, self.tokens, self.limiter = cfg, db, tokens, limiter
        self.peers, self.engine, self.discovery, self.fingerprint = peers, engine, discovery, fingerprint
        self.approvals: dict[str, dict] = {}
        self.admin_sessions: dict[str, float] = {}
        self.serving = ServeStats()
        self._ips = set(local_ipv4s())
        self._ips_at = time.time()

    def local_ips(self) -> set[str]:
        if time.time() - self._ips_at > 30:
            self._ips, self._ips_at = set(local_ipv4s()), time.time()
        return self._ips

    def shares_source(self) -> VirtualSource:
        return VirtualSource({s["name"]: s["path"] for s in self.cfg.get("shares") or []})


def create_app(ctx: Context) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    cfg = ctx.cfg

    def _tick():
        while True:
            time.sleep(1)
            ctx.serving.tick()
            now = time.time()
            for k, a in list(ctx.approvals.items()):
                if now - a["created"] > APPROVAL_TTL + 60:
                    ctx.approvals.pop(k, None)

    threading.Thread(target=_tick, daemon=True).start()

    # ------------------------------------------------------------------ errors
    @app.exception_handler(PeerError)
    async def _peer_err(request, e: PeerError):
        return JSONResponse({"detail": e.message, "peer_status": e.status}, status_code=502)

    @app.exception_handler(httpx.TransportError)
    async def _net_err(request, e):
        return JSONResponse({"detail": "Could not reach the other PC. Is Zap Drop running there, "
                                       "and is the firewall rule added?", "peer_status": 0}, status_code=502)

    @app.exception_handler(PathError)
    async def _path_err(request, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _key_err(request, e):
        return JSONResponse({"detail": str(e.args[0]) if e.args else "Not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def _val_err(request, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # ================================================================== PEER API
    def peer_auth(request: Request, allow_offer=True) -> dict:
        if cfg.get("mode") == "pin_tls" and request.url.scheme != "https":
            raise HTTPException(403, "This PC requires an encrypted connection")
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.lower().startswith("bearer ") else None
        info = ctx.tokens.check(tok)
        if info:
            if not cfg.get("hosting"):
                raise HTTPException(403, "This PC is not sharing right now")
            return {"type": "session", "client_id": info["client_id"], "alias": info["alias"],
                    "source": ctx.shares_source()}
        if tok and allow_offer:
            offer = ctx.db.offer_by_token(tok)
            if offer:
                return {"type": "offer", "alias": "push", "source": VirtualSource(offer[1])}
        raise HTTPException(401, "Not authorized. Connect again.")

    @app.get("/peer/hello")
    def peer_hello():
        return {"app": "zapdrop", "version": VERSION, "id": cfg.get("id"), "alias": cfg.get("alias"),
                "port": cfg.port, "tls_port": cfg.tls_port, "mode": cfg.get("mode"),
                "hosting": bool(cfg.get("hosting"))}

    @app.post("/peer/auth")
    def peer_authenticate(request: Request, body: dict = Body(...)):
        mode = cfg.get("mode")
        ip = request.client.host
        if not cfg.get("hosting"):
            raise HTTPException(403, "This PC is not sharing right now")
        if mode == "pin_tls" and request.url.scheme != "https":
            raise HTTPException(403, "This PC requires an encrypted connection")
        wait = ctx.limiter.locked_for(ip)
        if wait:
            raise HTTPException(429, f"Too many wrong attempts. Try again in {wait} s.")
        client_id = str(body.get("client_id", ""))[:64]
        alias = str(body.get("alias", "Unknown"))[:60]
        if not client_id:
            raise HTTPException(400, "Missing client id")
        if mode in ("pin", "pin_tls"):
            if not cfg.get("pin"):
                raise HTTPException(403, "The other PC has not set a PIN yet")
            if not check_secret(str(body.get("pin") or ""), cfg.get("pin")):
                ctx.limiter.fail(ip)
                raise HTTPException(401, "Wrong PIN")
            ctx.limiter.ok(ip)
            return {"token": ctx.tokens.issue(client_id, alias, ip)}
        if mode == "open":
            return {"token": ctx.tokens.issue(client_id, alias, ip)}
        # approve mode: wait for the person at this PC
        for rid, a in ctx.approvals.items():
            if a["client_id"] == client_id and a["status"] == "pending":
                return {"pending": rid}
        rid = uuid.uuid4().hex
        ctx.approvals[rid] = {"id": rid, "client_id": client_id, "alias": alias, "ip": ip,
                              "created": time.time(), "status": "pending", "token": None}
        return {"pending": rid}

    @app.get("/peer/auth/poll")
    def peer_auth_poll(req: str):
        a = ctx.approvals.get(req)
        if not a:
            raise HTTPException(404, "Request expired")
        if a["status"] == "pending" and time.time() - a["created"] > APPROVAL_TTL:
            a["status"] = "denied"
        res = {"status": a["status"]}
        if a["status"] == "approved":
            res["token"] = a["token"]
            ctx.approvals.pop(req, None)
        elif a["status"] == "denied":
            ctx.approvals.pop(req, None)
        return res

    @app.get("/peer/browse")
    def peer_browse(request: Request, path: str = ""):
        who = peer_auth(request)
        try:
            return {"entries": who["source"].browse(path)}
        except OSError as e:
            raise HTTPException(404, f"Cannot open folder: {e.strerror or e}")

    @app.post("/peer/manifest")
    def peer_manifest(request: Request, body: dict = Body(...)):
        who = peer_auth(request)
        return who["source"].manifest(list(body.get("paths", [])))

    @app.get("/peer/chunk")
    def peer_chunk(request: Request, path: str, offset: int, length: int, size: int, mtime: float):
        who = peer_auth(request)
        if offset < 0 or length <= 0 or length > MAX_CHUNK:
            raise HTTPException(400, "Bad range")
        real = who["source"].resolve(path)
        try:
            st = os.stat(real)
        except OSError:
            raise HTTPException(404, "File no longer exists")
        if st.st_size != size or abs(st.st_mtime - mtime) > 2:
            raise HTTPException(409, "File changed on the sending PC after the transfer started")
        try:
            with open(real, "rb") as f:
                f.seek(offset)
                data = f.read(length)
        except OSError as e:
            raise HTTPException(410, f"Cannot read file: {e.strerror or e}")
        ctx.serving.add(who["alias"], len(data))
        return Response(data, media_type="application/octet-stream", headers={"X-ZapDrop-Hash": hash_hex(data)})

    @app.post("/peer/batch")
    def peer_batch(request: Request, body: dict = Body(...)):
        who = peer_auth(request)
        src = who["source"]
        parts, sizes, total = [], [], 0
        for item in list(body.get("files", []))[:BATCH_FILES]:
            try:
                real = src.resolve(item["path"])
                st = os.stat(real)
                if st.st_size != int(item["size"]) or abs(st.st_mtime - float(item["mtime"])) > 2 \
                        or total + st.st_size > MAX_CHUNK:
                    sizes.append(-1)
                    continue
                with open(real, "rb") as f:
                    data = f.read()
                if len(data) != st.st_size:
                    sizes.append(-1)
                    continue
                parts.append(data)
                sizes.append(len(data))
                total += len(data)
            except (OSError, PathError, KeyError, ValueError):
                sizes.append(-1)
        blob = b"".join(parts)
        ctx.serving.add(who["alias"], len(blob))
        return Response(blob, media_type="application/octet-stream",
                        headers={"X-ZapDrop-Hash": hash_hex(blob), "X-ZapDrop-Sizes": ",".join(map(str, sizes))})

    def _push_job(request: Request, job_id: str):
        who = peer_auth(request, allow_offer=False)
        job = ctx.engine.jobs.get(job_id)
        if not job or job.meta["kind"] != "push_in" or job.meta.get("owner") != who["client_id"]:
            raise HTTPException(404, "Transfer not found")
        return job

    @app.post("/peer/push/prepare")
    def peer_push_prepare(request: Request, body: dict = Body(...)):
        who = peer_auth(request, allow_offer=False)
        for k in ("offer_token", "port", "tls_port"):
            if k not in body:
                raise HTTPException(400, f"Missing {k}")
        job = ctx.engine.create_push_in(body, request.client.host, who["client_id"])
        return {"job_id": job.id, "conflicts": job.conflicts[:5000], "conflicts_total": len(job.conflicts),
                "dest": job.meta["dest"]}

    @app.post("/peer/push/start")
    def peer_push_start(request: Request, body: dict = Body(...)):
        job = _push_job(request, body.get("job_id", ""))
        ctx.engine.start_job(job.id, body.get("default", "rename"), body.get("per_file"))
        return {"ok": True}

    @app.get("/peer/push/status/{job_id}")
    def peer_push_status(request: Request, job_id: str):
        job = _push_job(request, job_id)
        d = job.to_dict()
        d["elapsed"] = job.meta.get("elapsed", 0)
        return d

    @app.post("/peer/push/control/{job_id}")
    def peer_push_control(request: Request, job_id: str, body: dict = Body(...)):
        job = _push_job(request, job_id)
        action = body.get("action")
        if action not in ("pause", "resume", "cancel"):
            raise HTTPException(400, "Bad action")
        ctx.engine.control(job.id, action)
        return {"ok": True}

    # ================================================================== UI API
    def _is_local(request: Request) -> bool:
        ip = request.client.host if request.client else ""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        host = (request.url.hostname or "").lower().strip("[]")
        local_ip = ip in ("127.0.0.1", "::1") or ip in ctx.local_ips()
        allowed_hosts = {"localhost", "127.0.0.1", "::1", socket.gethostname().lower()} | ctx.local_ips()
        return local_ip and host in allowed_hosts  # host check blocks DNS-rebinding tricks

    def _authed(request: Request) -> bool:
        if _is_local(request):
            return True
        sid = request.cookies.get("zapdrop_session")
        exp = ctx.admin_sessions.get(sid or "")
        return bool(exp and exp > time.time())

    @app.middleware("http")
    async def ui_guard(request: Request, call_next):
        p = request.url.path
        if p.startswith("/api/"):
            if request.url.scheme == "https":
                return JSONResponse({"detail": "Use the http address for the UI"}, status_code=404)
            # custom header: browsers block cross-site pages from sending it
            if request.headers.get("x-zapdrop") != "1":
                return JSONResponse({"detail": "Missing header"}, status_code=403)
            if p not in ("/api/session", "/api/login") and not _authed(request):
                return JSONResponse({"detail": "Login required", "login": True}, status_code=401)
        return await call_next(request)

    @app.get("/api/session")
    def api_session(request: Request):
        return {"authed": _authed(request), "local": _is_local(request), "admin_set": bool(cfg.get("admin")),
                "alias": cfg.get("alias")}

    @app.post("/api/login")
    def api_login(request: Request, body: dict = Body(...)):
        ip = request.client.host
        wait = ctx.limiter.locked_for("ui:" + ip)
        if wait:
            raise HTTPException(429, f"Too many attempts. Try again in {wait} s.")
        if not cfg.get("admin"):
            raise HTTPException(403, "Remote access is off. Set an admin password on the host PC first.")
        if not check_secret(str(body.get("password", "")), cfg.get("admin")):
            ctx.limiter.fail("ui:" + ip)
            raise HTTPException(401, "Wrong password")
        ctx.limiter.ok("ui:" + ip)
        sid = secrets.token_urlsafe(32)
        ctx.admin_sessions[sid] = time.time() + ADMIN_SESSION_TTL
        resp = JSONResponse({"ok": True})
        resp.set_cookie("zapdrop_session", sid, httponly=True, samesite="strict", max_age=ADMIN_SESSION_TTL)
        return resp

    @app.post("/api/logout")
    def api_logout(request: Request):
        ctx.admin_sessions.pop(request.cookies.get("zapdrop_session", ""), None)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("zapdrop_session")
        return resp

    def _me() -> dict:
        return {"id": cfg.get("id"), "alias": cfg.get("alias"), "hosting": bool(cfg.get("hosting")),
                "mode": cfg.get("mode"), "has_pin": bool(cfg.get("pin")), "has_admin": bool(cfg.get("admin")),
                "port": cfg.port, "tls_port": cfg.tls_port, "ips": sorted(ctx.local_ips()),
                "receive_dir": cfg.get("receive_dir"), "shares": cfg.get("shares") or [],
                "fingerprint": ctx.fingerprint, "discovery_error": ctx.discovery.error, "version": VERSION}

    @app.get("/api/state")
    def api_state():
        now = time.time()
        approvals = [{k: a[k] for k in ("id", "alias", "ip", "created")} for a in ctx.approvals.values()
                     if a["status"] == "pending" and now - a["created"] < APPROVAL_TTL]
        return {"me": _me(), "peers": ctx.peers.list(), "jobs": ctx.engine.list(), "approvals": approvals,
                "serving": ctx.serving.list(), "clients": ctx.tokens.clients()}

    @app.post("/api/settings")
    def api_settings(body: dict = Body(...)):
        upd, revoke = {}, False
        if "alias" in body:
            alias = str(body["alias"]).strip()[:40]
            if not alias:
                raise ValueError("Alias cannot be empty")
            upd["alias"] = alias
        if "pin" in body and body["pin"] is not None:
            pin = str(body["pin"]).strip()
            if len(pin) < 4:
                raise ValueError("PIN must be at least 4 characters")
            upd["pin"] = hash_secret(pin)
            revoke = True
        if "mode" in body:
            if body["mode"] not in MODES:
                raise ValueError("Unknown access mode")
            if body["mode"] != cfg.get("mode"):
                upd["mode"] = body["mode"]
                revoke = True
        if "receive_dir" in body:
            d = os.path.abspath(str(body["receive_dir"]))
            Path(d).mkdir(parents=True, exist_ok=True)
            upd["receive_dir"] = d
        if "admin_password" in body:
            pw = str(body["admin_password"] or "")
            if pw and len(pw) < 6:
                raise ValueError("Admin password must be at least 6 characters")
            upd["admin"] = hash_secret(pw) if pw else None
            ctx.admin_sessions.clear()
        if "hosting" in body:
            upd["hosting"] = bool(body["hosting"])
        mode = upd.get("mode", cfg.get("mode"))
        has_pin = bool(upd.get("pin") or cfg.get("pin"))
        if upd.get("hosting", cfg.get("hosting")) and mode in ("pin", "pin_tls") and not has_pin:
            raise ValueError("Set a PIN before hosting in PIN mode")
        cfg.update(**upd)
        if revoke:
            ctx.tokens.revoke_all()
        ctx.discovery.announce_now()
        return _me()

    @app.post("/api/shares")
    def api_add_share(body: dict = Body(...)):
        path = os.path.abspath(str(body.get("path", "")))
        if not os.path.isdir(path):
            raise ValueError("Folder not found")
        shares = cfg.get("shares") or []
        if any(os.path.normcase(s["path"]) == os.path.normcase(path) for s in shares):
            raise ValueError("Folder is already shared")
        name = str(body.get("name") or os.path.basename(path.rstrip("\\/")) or path[0]).strip()
        name = name.replace("/", "_").replace("\\", "_").replace(":", "")[:60] or "Share"
        base, n = name, 1
        while any(s["name"] == name for s in shares):
            n += 1
            name = f"{base} ({n})"
        shares.append({"name": name, "path": path})
        cfg.update(shares=shares)
        return _me()

    @app.delete("/api/shares/{name}")
    def api_remove_share(name: str):
        cfg.update(shares=[s for s in cfg.get("shares") or [] if s["name"] != name])
        return _me()

    @app.post("/api/revoke")
    def api_revoke():
        ctx.tokens.revoke_all()
        return {"ok": True}

    @app.post("/api/approvals/{rid}")
    def api_approve(rid: str, body: dict = Body(...)):
        a = ctx.approvals.get(rid)
        if not a or a["status"] != "pending":
            raise KeyError("Request expired")
        if body.get("allow"):
            a["token"] = ctx.tokens.issue(a["client_id"], a["alias"], a["ip"])
            a["status"] = "approved"
        else:
            a["status"] = "denied"
        return {"ok": True}

    # ---- local file system (this PC) ----
    @app.get("/api/local/browse")
    def api_local_browse(path: str = ""):
        if not path:
            home = Path.home()
            quick = [{"name": n, "path": str(home / n), "is_dir": True, "size": 0, "mtime": 0}
                     for n in ("Desktop", "Documents", "Downloads", "Pictures", "Videos") if (home / n).is_dir()]
            return {"path": "", "parent": None, "entries": list_drives(), "quick": quick}
        path = os.path.abspath(path)
        try:
            entries = list_dir(path)
        except OSError as e:
            raise ValueError(f"Cannot open folder: {e.strerror or e}")
        parent = os.path.dirname(path)
        return {"path": path, "parent": "" if parent == path else parent, "entries": entries}

    @app.post("/api/local/mkdir")
    def api_local_mkdir(body: dict = Body(...)):
        name = str(body.get("name", "")).strip()
        if not name or any(c in name for c in '\\/:*?"<>|'):
            raise ValueError("Invalid folder name")
        p = Path(body.get("path", ""), name)
        p.mkdir(parents=False, exist_ok=False)
        return {"path": str(p)}

    # ---- peers ----
    def _peer(pid):
        p = ctx.peers.get(pid)
        if not p:
            raise KeyError("Device not found")
        return p

    @app.post("/api/peers")
    def api_add_peer(body: dict = Body(...)):
        return ctx.peers.add_manual(str(body.get("address", ""))).to_dict()

    @app.delete("/api/peers/{pid}")
    def api_remove_peer(pid: str):
        ctx.peers.remove(pid)
        return {"ok": True}

    @app.post("/api/peers/{pid}/connect")
    def api_connect(pid: str, body: dict = Body(default={})):
        return ctx.peers.connect(pid, body.get("pin"))

    @app.post("/api/peers/{pid}/disconnect")
    def api_disconnect(pid: str):
        ctx.peers.disconnect(pid)
        return {"ok": True}

    @app.get("/api/peers/{pid}/browse")
    def api_peer_browse(pid: str, path: str = ""):
        p = _peer(pid)
        try:
            with ctx.peers.client(p) as c:
                res = c.get_json("/peer/browse", path=path)
                ctx.peers.remember_fingerprint(pid, c.seen_fingerprint)
                return res
        except PeerError as e:
            if e.status in (401, 403) and "encrypted" not in e.message:
                ctx.peers.mark_unauthorized(pid, e.message)
            raise

    # ---- transfers ----
    def _draft_response(job):
        if not job.conflicts:
            ctx.engine.start_job(job.id, "rename")
        d = job.to_dict()
        d["conflicts"] = job.conflicts[:5000]
        d["conflicts_total"] = len(job.conflicts)
        return d

    @app.post("/api/pull")
    def api_pull(body: dict = Body(...)):
        paths = list(body.get("paths", []))
        if not paths:
            raise ValueError("Select something to download")
        if not body.get("dest"):
            raise ValueError("Choose a destination folder on this PC")
        return _draft_response(ctx.engine.create_pull(_peer(body.get("peer_id")), paths, body["dest"]))

    @app.post("/api/push")
    def api_push(body: dict = Body(...)):
        paths = list(body.get("paths", []))
        if not paths:
            raise ValueError("Select something to send")
        return _draft_response(ctx.engine.create_push_out(_peer(body.get("peer_id")), paths))

    @app.post("/api/jobs/{job_id}/start")
    def api_job_start(job_id: str, body: dict = Body(default={})):
        return ctx.engine.start_job(job_id, body.get("default", "rename"), body.get("per_file")).to_dict()

    @app.post("/api/jobs/{job_id}/discard")
    def api_job_discard(job_id: str):
        ctx.engine.discard_draft(job_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/{action}")
    def api_job_control(job_id: str, action: str):
        if action not in ("pause", "resume", "cancel", "remove"):
            raise ValueError("Bad action")
        j = ctx.engine.control(job_id, action)
        return j.to_dict() if j else {"ok": True}

    @app.get("/api/history")
    def api_history():
        return {"history": ctx.db.list_history()}

    @app.delete("/api/history")
    def api_clear_history():
        ctx.db.clear_history()
        return {"ok": True}

    # ---- static UI ----
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
