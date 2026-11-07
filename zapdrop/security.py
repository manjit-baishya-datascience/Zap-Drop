"""PIN/password hashing, issued tokens, brute-force limiting and the TLS certificate."""

import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

TOKEN_TTL = 7 * 24 * 3600


def hash_secret(secret: str, salt: str | None = None) -> dict:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return {"salt": salt, "hash": h.hex()}


def check_secret(secret: str, record: dict | None) -> bool:
    if not record or secret is None:
        return False
    return hmac.compare_digest(hash_secret(secret, record["salt"])["hash"], record["hash"])


class TokenStore:
    """Tokens this host has issued to other PCs (persisted so transfers survive restarts)."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._t: dict = {}
        if path.exists():
            try:
                self._t = json.loads(path.read_text("utf-8"))
            except Exception:
                self._t = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._t), "utf-8")
        os.replace(tmp, self.path)

    def issue(self, client_id: str, alias: str, ip: str) -> str:
        tok = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            # one token per client: replace older ones
            self._t = {k: v for k, v in self._t.items() if v["client_id"] != client_id and v["expires"] > now}
            self._t[tok] = {"client_id": client_id, "alias": alias, "ip": ip,
                            "created": now, "expires": now + TOKEN_TTL}
            self._save()
        return tok

    def check(self, tok: str | None) -> dict | None:
        if not tok:
            return None
        with self._lock:
            info = self._t.get(tok)
            if info and info["expires"] > time.time():
                return dict(info)
        return None

    def revoke_all(self):
        with self._lock:
            self._t = {}
            self._save()

    def clients(self) -> list:
        now = time.time()
        with self._lock:
            return [dict(v) for v in self._t.values() if v["expires"] > now]


class AttemptLimiter:
    """Locks an IP out for a while after too many wrong PINs/passwords."""

    def __init__(self, max_fails=5, window=300, lockout=120):
        self.max_fails, self.window, self.lockout = max_fails, window, lockout
        self._f: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, ip: str) -> int:
        now = time.time()
        with self._lock:
            fails = [t for t in self._f.get(ip, []) if now - t < self.window]
            self._f[ip] = fails
            if len(fails) >= self.max_fails:
                remaining = self.lockout - (now - fails[-1])
                if remaining > 0:
                    return int(remaining) + 1
                self._f[ip] = []
        return 0

    def fail(self, ip: str):
        with self._lock:
            self._f.setdefault(ip, []).append(time.time())

    def ok(self, ip: str):
        with self._lock:
            self._f.pop(ip, None)


def ensure_cert(data_dir: Path) -> tuple[str, str, str]:
    """Create (once) a self-signed certificate. Returns (certfile, keyfile, sha256 fingerprint)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = data_dir / "cert.pem", data_dir / "key.pem"
    if not (cert_path.exists() and key_path.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Zap Drop")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    from cryptography.hazmat.primitives import serialization as s
    fp = hashlib.sha256(cert.public_bytes(s.Encoding.DER)).hexdigest()
    return str(cert_path), str(key_path), fp
