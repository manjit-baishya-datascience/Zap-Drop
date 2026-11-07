"""Other Zap Drop PCs: discovered or added by address, plus our access tokens for them."""

import json
import os
import threading
import time
from pathlib import Path

from .client import PeerClient, PeerError

ONLINE_TIMEOUT = 8.0


class Peer:
    def __init__(self, pid: str, alias: str, host: str, port: int, tls_port: int, mode: str):
        self.id, self.alias, self.host, self.port, self.tls_port, self.mode = pid, alias, host, port, tls_port, mode
        self.last_seen = time.time()
        self.online = True
        self.manual_address: str | None = None
        self.status = "disconnected"  # disconnected | pending | connected | denied
        self.error = ""
        self.pending_req: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "alias": self.alias, "host": self.host, "port": self.port,
                "mode": self.mode, "online": self.online, "manual": bool(self.manual_address),
                "status": self.status, "error": self.error}


class PeerRegistry:
    def __init__(self, cfg, data_dir: Path):
        self.cfg = cfg
        self._lock = threading.RLock()
        self.peers: dict[str, Peer] = {}
        self.tokens_path = data_dir / "peer_tokens.json"
        self.tokens: dict[str, str] = {}
        if self.tokens_path.exists():
            try:
                self.tokens = json.loads(self.tokens_path.read_text("utf-8"))
            except Exception:
                self.tokens = {}
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name="peers", daemon=True).start()

    def stop(self):
        self._stop.set()

    # ---------- tokens ----------
    def _save_tokens(self):
        tmp = self.tokens_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.tokens), "utf-8")
        os.replace(tmp, self.tokens_path)

    def set_token(self, pid: str, token: str | None):
        with self._lock:
            if token:
                self.tokens[pid] = token
            else:
                self.tokens.pop(pid, None)
            self._save_tokens()
            p = self.peers.get(pid)
            if p:
                p.status = "connected" if token else "disconnected"
                p.pending_req = None

    def mark_unauthorized(self, pid: str, error: str = ""):
        self.set_token(pid, None)
        p = self.peers.get(pid)
        if p:
            p.error = error

    # ---------- peers ----------
    def _upsert(self, info: dict, host: str) -> Peer:
        with self._lock:
            pid = info["id"]
            p = self.peers.get(pid)
            if p is None:
                p = Peer(pid, info.get("alias", "?"), host, int(info["port"]),
                         int(info.get("tls_port") or int(info["port"]) + 1), info.get("mode", "pin"))
                self.peers[pid] = p
                if pid in self.tokens:
                    p.status = "connected"
            p.alias = info.get("alias", p.alias)
            p.host = host
            p.port = int(info["port"])
            p.tls_port = int(info.get("tls_port") or p.port + 1)
            p.mode = info.get("mode", p.mode)
            p.last_seen = time.time()
            p.online = info.get("hosting", True)
            return p

    def on_discovery(self, msg: dict, ip: str):
        if msg.get("type") == "bye":
            with self._lock:
                p = self.peers.get(msg.get("id"))
                if p:
                    p.online = False
            return
        self._upsert(msg, ip)

    def get(self, pid: str) -> Peer | None:
        with self._lock:
            return self.peers.get(pid)

    def list(self) -> list[dict]:
        now = time.time()
        with self._lock:
            out = []
            for p in self.peers.values():
                if not p.manual_address and now - p.last_seen > ONLINE_TIMEOUT:
                    p.online = False
                if not p.online and not p.manual_address and now - p.last_seen > 600:
                    continue  # forget long-gone discovered peers
                out.append(p.to_dict())
            out.sort(key=lambda d: (not d["online"], d["alias"].lower()))
            return out

    @staticmethod
    def parse_address(address: str) -> tuple[str, int]:
        address = address.strip()
        for prefix in ("http://", "https://"):
            if address.startswith(prefix):
                address = address[len(prefix):]
        address = address.rstrip("/")
        if address.count(":") == 1:
            host, port = address.split(":")
            return host, int(port)
        return address, 8765

    def hello(self, host: str, port: int) -> dict:
        with PeerClient(host, port) as c:
            return c.get_json("/peer/hello")

    def add_manual(self, address: str) -> Peer:
        host, port = self.parse_address(address)
        info = self.hello(host, port)
        if info.get("id") == self.cfg.get("id"):
            raise PeerError(400, "That address is this PC")
        p = self._upsert(info, host)
        p.manual_address = f"{host}:{port}"
        manual = self.cfg.get("manual_peers") or []
        if p.manual_address not in manual:
            manual.append(p.manual_address)
            self.cfg.update(manual_peers=manual)
        return p

    def remove(self, pid: str):
        with self._lock:
            p = self.peers.pop(pid, None)
            self.tokens.pop(pid, None)
            self._save_tokens()
        if p and p.manual_address:
            manual = [m for m in (self.cfg.get("manual_peers") or []) if m != p.manual_address]
            self.cfg.update(manual_peers=manual)
        fps = self.cfg.get("known_fingerprints") or {}
        if fps.pop(pid, None):
            self.cfg.update(known_fingerprints=fps)

    def client(self, p: Peer, with_token=True, connections=8) -> PeerClient:
        fps = self.cfg.get("known_fingerprints") or {}
        return PeerClient(p.host, p.port, p.tls_port, p.mode == "pin_tls", fps.get(p.id),
                          self.tokens.get(p.id) if with_token else None, connections)

    def remember_fingerprint(self, pid: str, fp: str | None):
        if not fp:
            return
        fps = self.cfg.get("known_fingerprints") or {}
        if fps.get(pid) != fp:
            fps[pid] = fp
            self.cfg.update(known_fingerprints=fps)

    def _refresh_mode(self, p: Peer):
        """Re-read hello so we know the current access mode before authenticating."""
        try:
            info = self.hello(p.host, p.port)
            self._upsert(info, p.host)
        except Exception:
            pass

    def connect(self, pid: str, pin: str | None = None) -> dict:
        p = self.get(pid)
        if not p:
            raise PeerError(404, "Unknown device")
        self._refresh_mode(p)
        with self.client(p, with_token=False) as c:
            res = c.post_json("/peer/auth", {"client_id": self.cfg.get("id"),
                                             "alias": self.cfg.get("alias"), "pin": pin})
            self.remember_fingerprint(pid, c.seen_fingerprint)
        p.error = ""
        if res.get("token"):
            self.set_token(pid, res["token"])
        elif res.get("pending"):
            p.status, p.pending_req = "pending", res["pending"]
        return p.to_dict()

    def disconnect(self, pid: str):
        self.set_token(pid, None)

    # ---------- background ----------
    def _poll_pending(self, p: Peer):
        try:
            with self.client(p, with_token=False) as c:
                res = c.get_json("/peer/auth/poll", req=p.pending_req)
        except PeerError as e:
            if e.status == 404:
                p.status, p.pending_req, p.error = "disconnected", None, "Request expired"
            return
        except Exception:
            return
        if res.get("status") == "approved" and res.get("token"):
            self.set_token(p.id, res["token"])
        elif res.get("status") == "denied":
            p.status, p.pending_req, p.error = "denied", None, "The other PC declined the request"

    def _loop(self):
        tick = 0
        for addr in self.cfg.get("manual_peers") or []:
            try:
                self.add_manual(addr)
            except Exception:
                pass
        while not self._stop.wait(1.0):
            tick += 1
            with self._lock:
                peers = list(self.peers.values())
            for p in peers:
                if p.status == "pending" and p.pending_req:
                    self._poll_pending(p)
            if tick % 5 == 0:
                for p in peers:
                    if p.manual_address:
                        try:
                            info = self.hello(p.host, p.port)
                            self._upsert(info, p.host)
                        except Exception:
                            p.online = False
                # manual peers that were never reachable at startup
                known = {p.manual_address for p in peers if p.manual_address}
                for addr in self.cfg.get("manual_peers") or []:
                    if addr not in known:
                        try:
                            self.add_manual(addr)
                        except Exception:
                            pass
