"""LAN discovery: hosting PCs broadcast their alias over UDP; everyone listens."""

import json
import socket
import threading
import time

from .config import DISCOVERY_PORT

ANNOUNCE_EVERY = 2.0


def local_ipv4s() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    # The address used for the default route (no packets are actually sent).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


class Discovery:
    def __init__(self, cfg, on_message):
        self.cfg = cfg
        self.on_message = on_message
        self._stop = threading.Event()
        self._was_hosting = False
        self.error = ""

    def start(self):
        threading.Thread(target=self._listen, name="discovery-listen", daemon=True).start()
        threading.Thread(target=self._announce_loop, name="discovery-announce", daemon=True).start()

    def stop(self):
        self._stop.set()
        if self.cfg.get("hosting"):
            self._send("bye")

    def _listen(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", DISCOVERY_PORT))
        except OSError as e:
            self.error = f"Discovery port {DISCOVERY_PORT} unavailable: {e}"
            return
        s.settimeout(1.0)
        my_id = self.cfg.get("id")
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.5)
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("app") != "zapdrop" or msg.get("id") == my_id:
                continue
            try:
                self.on_message(msg, addr[0])
            except Exception:
                pass

    def _payload(self, kind: str) -> bytes:
        return json.dumps({
            "app": "zapdrop", "v": 1, "type": kind, "id": self.cfg.get("id"),
            "alias": self.cfg.get("alias"), "port": self.cfg.port, "tls_port": self.cfg.tls_port,
            "mode": self.cfg.get("mode"),
        }).encode("utf-8")

    def _send(self, kind: str):
        payload = self._payload(kind)
        targets = local_ipv4s() or ["0.0.0.0"]
        for ip in targets:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind((ip, 0))  # send out through this specific network adapter
                s.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
                s.close()
            except OSError:
                continue

    def announce_now(self):
        threading.Thread(target=self._tick, daemon=True).start()

    def _tick(self):
        hosting = bool(self.cfg.get("hosting"))
        if hosting:
            self._send("announce")
        elif self._was_hosting:
            self._send("bye")
        self._was_hosting = hosting

    def _announce_loop(self):
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(ANNOUNCE_EVERY)
