"""Persistent settings stored as JSON in the data folder."""

import copy
import json
import os
import socket
import threading
import uuid
from pathlib import Path

# Access modes a host can choose.
MODES = ("pin", "pin_tls", "approve", "open")
DISCOVERY_PORT = 47821
DEFAULT_PORT = 8765


def default_receive_dir() -> str:
    return str(Path.home() / "Downloads" / "Zap Drop")


class Config:
    def __init__(self, data_dir: Path, port: int | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.json"
        self._lock = threading.RLock()
        self._d: dict = {}
        if self.path.exists():
            try:
                self._d = json.loads(self.path.read_text("utf-8"))
            except Exception:
                self._d = {}
        defaults = {
            "id": uuid.uuid4().hex,
            "alias": socket.gethostname(),
            "hosting": False,
            "mode": "pin",
            "pin": None,          # {"salt", "hash"} of the host PIN
            "admin": None,        # {"salt", "hash"} of the remote-UI admin password
            "receive_dir": default_receive_dir(),
            "shares": [],         # [{"name", "path"}]
            "port": DEFAULT_PORT,
            "known_fingerprints": {},  # peer id -> TLS cert fingerprint (trust on first use)
            "manual_peers": [],   # ["host:port"]
        }
        for k, v in defaults.items():
            self._d.setdefault(k, v)
        if port:
            self._d["port"] = int(port)
        if self._d["mode"] not in MODES:
            self._d["mode"] = "pin"
        self.save()

    @property
    def port(self) -> int:
        return int(self._d["port"])

    @property
    def tls_port(self) -> int:
        return self.port + 1

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self._d.get(key, default))

    def update(self, **kw):
        with self._lock:
            self._d.update(kw)
            self.save()

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._d, indent=2), "utf-8")
            os.replace(tmp, self.path)
