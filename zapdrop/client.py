"""HTTP client used to talk to another Zap Drop PC (optionally over TLS with a pinned certificate)."""

import hashlib
import ssl

import httpx

from . import VERSION


class PeerError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _tls_context() -> ssl.SSLContext:
    # Self-signed certificates: we skip CA validation and pin the fingerprint instead.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class PeerClient:
    def __init__(self, host: str, port: int, tls_port: int | None = None, use_tls: bool = False,
                 fingerprint: str | None = None, token: str | None = None, connections: int = 8):
        self.use_tls = use_tls
        self.fingerprint = fingerprint
        self.seen_fingerprint = None
        h = f"[{host}]" if ":" in host else host
        base = f"https://{h}:{tls_port}" if use_tls else f"http://{h}:{port}"
        headers = {"User-Agent": f"ZapDrop/{VERSION}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(
            base_url=base, headers=headers, verify=_tls_context() if use_tls else True,
            timeout=httpx.Timeout(60.0, connect=5.0),
            limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections))

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _check(self, r: httpx.Response):
        if self.use_tls:
            fp = None
            stream = r.extensions.get("network_stream")
            if stream is not None:
                sslobj = stream.get_extra_info("ssl_object")
                if sslobj is not None:
                    der = sslobj.getpeercert(True)  # positional: works for SSLSocket and _SSLSocket
                    if der:
                        fp = hashlib.sha256(der).hexdigest()
            if fp is None:
                raise PeerError(495, "Could not read the other PC's certificate")
            if self.fingerprint and fp != self.fingerprint:
                raise PeerError(495, "The other PC's security certificate changed. "
                                     "If you reinstalled Zap Drop there, remove and re-add it.")
            self.seen_fingerprint = fp
        if r.status_code >= 400:
            try:
                msg = r.json().get("detail", r.text)
            except Exception:
                msg = r.text or r.reason_phrase
            raise PeerError(r.status_code, str(msg))

    def get_json(self, _url, **params):
        r = self.http.get(_url, params=params or None)
        self._check(r)
        return r.json()

    def post_json(self, path, body=None, timeout=None):
        r = self.http.post(path, json=body or {}, timeout=timeout or httpx.USE_CLIENT_DEFAULT)
        self._check(r)
        return r.json()

    def get_bytes(self, path, params) -> tuple[bytes, httpx.Headers]:
        r = self.http.get(path, params=params)
        self._check(r)
        return r.content, r.headers

    def post_bytes(self, path, body) -> tuple[bytes, httpx.Headers]:
        r = self.http.post(path, json=body)
        self._check(r)
        return r.content, r.headers
