"""Start Zap Drop:  python -m zapdrop [--port 8765] [--data DIR] [--no-browser]"""

import argparse
import asyncio
import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import APP_NAME, VERSION
from .config import Config
from .db import DB
from .discovery import Discovery, local_ipv4s
from .engine import Engine
from .peers import PeerRegistry
from .security import AttemptLimiter, TokenStore, ensure_cert
from .server import Context, create_app


def default_data_dir() -> Path:
    if getattr(sys, "frozen", False):  # packaged .exe
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return Path(__file__).resolve().parent.parent / "data"


def main():
    ap = argparse.ArgumentParser(prog="zapdrop", description="Fast LAN file transfer")
    ap.add_argument("--port", type=int, help="HTTP port (TLS uses port+1). Default 8765")
    ap.add_argument("--data", help="Folder for settings, history and resume state")
    ap.add_argument("--no-browser", action="store_true", help="Do not open the browser on start")
    args = ap.parse_args()

    data_dir = Path(args.data).resolve() if args.data else default_data_dir()
    cfg = Config(data_dir, args.port)
    certfile, keyfile, fingerprint = ensure_cert(data_dir)

    # carry over the database from earlier project names (Ferry, Net Share, LANDrop)
    for old_name in ("ferry", "netshare", "landrop"):
        if (data_dir / f"{old_name}.db").exists() and not (data_dir / "zapdrop.db").exists():
            for suffix in ("", "-wal", "-shm"):
                old = data_dir / f"{old_name}.db{suffix}"
                if old.exists():
                    old.rename(data_dir / f"zapdrop.db{suffix}")
    db = DB(data_dir / "zapdrop.db")
    tokens = TokenStore(data_dir / "issued_tokens.json")
    peers = PeerRegistry(cfg, data_dir)
    engine = Engine(db, cfg, peers, fingerprint)
    discovery = Discovery(cfg, peers.on_discovery)
    ctx = Context(cfg, db, tokens, AttemptLimiter(), peers, engine, discovery, fingerprint)
    app = create_app(ctx)

    discovery.start()
    peers.start()
    engine.start()

    url = f"http://localhost:{cfg.port}"
    print(f"\n  {APP_NAME} {VERSION}  -  '{cfg.get('alias')}'")
    print(f"  Open on this PC:      {url}")
    for ip in local_ipv4s():
        print(f"  Open from the LAN:    http://{ip}:{cfg.port}   (needs the admin password)")
    print(f"  Data folder:          {data_dir}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    servers = [
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.port, log_level="warning",
                                      access_log=False, timeout_keep_alive=30)),
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=cfg.tls_port, log_level="warning",
                                      access_log=False, timeout_keep_alive=30,
                                      ssl_certfile=certfile, ssl_keyfile=keyfile)),
    ]

    async def serve():
        await asyncio.gather(*(s.serve() for s in servers))

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    finally:
        print("  Stopping... (unfinished transfers can be resumed next time)")
        discovery.stop()
        peers.stop()
        engine.stop()


if __name__ == "__main__":
    main()
