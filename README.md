# Zap Drop

Fast file transfer between Windows PCs on the same local network. You run it on both PCs and control it from your browser.

- **Pull:** browse folders the other PC shares and download them.
- **Push:** send files or folders straight into the other PC's receive folder.
- **Discovery:** PCs find each other on the network by alias. You can also add one by IP.
- **Speed:** large files are split into 16 MB chunks fetched in parallel, and small files are bundled. A 1 Gbps link (~110 MB/s) stays full.
- **Integrity:** every chunk is checked with an xxh3-128 hash, so corrupted data is detected and re-downloaded.
- **Resume:** transfers continue where they stopped, even after a crash, a network drop or a reboot.
- **Conflicts:** when a file already exists, you choose Keep both, Replace or Skip, per file or for all.
- **Queue and history:** live speed and time remaining, plus a log of past transfers.
- **Access modes:** each host chooses PIN, PIN + TLS encryption, Approve each request, or Open.

## Setup (on each PC)

1. Copy this folder to the PC.
2. Run **`setup.bat`**. It finds Python 3.10+ or offers to install it, then installs the packages into `.venv`.
3. Run **`firewall.bat`** once. It asks for admin rights and opens ports 8765–8766 TCP and 47821 UDP on Private networks.
   Both PCs must be on a **Private** network. If Windows marked yours as Public, change it in
   *Settings → Network & Internet → Ethernet → your network → Private*.
4. Start Zap Drop with **`run.bat`**. Your browser opens at `http://localhost:8765`.
   If Windows shows a firewall prompt for Python, click **Allow** for private networks.

## Using it

1. On the PC that will be accessed, open **Settings**. Set a name, choose an access mode, set a PIN, and turn **Hosting** on.
   Optionally share some folders, and choose the receive folder (default: `Downloads\Zap Drop`).
2. On the other PC, that name appears under **Devices**. Click it and connect (enter the PIN, or wait for approval).
3. In the **Files** tab:
   - Left pane is this PC, right pane is the other PC's shared folders.
   - **Download:** open the destination folder on the left, select items on the right, then click **← Download here**.
     You can also drag them onto the left pane or onto a folder in it.
   - **Send:** select items on the left, then click **Send to … →**. You can also drag them onto the right pane or onto the device in the sidebar.
     Sent files land in the other PC's receive folder.
4. Watch progress in **Transfers**. Pause, resume or cancel from there. If the connection drops, the transfer
   retries automatically. If Zap Drop was closed, press **Resume**.

Transfers work both ways: turn on hosting on both PCs if each should be able to browse the other.

### Access modes

| Mode | What it does |
|---|---|
| PIN | Others need your PIN once; access lasts 7 days, or until you change the PIN or mode. |
| PIN + encryption | Same as PIN, and all traffic goes over TLS on port 8766. The certificate is pinned on first use. |
| Approve requests | You see a pop-up and choose Allow or Decline for each new device. |
| Open | Anyone on your network can connect. Use this only on a network you trust. |

### Controlling a PC from another device

Under **Settings → Control this PC from other devices**, set an admin password. Then open
`http://<that PC's IP>:8765` on a phone or laptop and sign in. Without a password, the UI only works on the PC itself.

## Tips for maximum speed

- Use wired Gigabit Ethernet on both PCs. Wi-Fi is usually the bottleneck.
- Copying to or from an HDD is limited by the disk, often 100–200 MB/s or less for many small files.
- For the fastest transfers, use plain **PIN** mode rather than PIN + encryption on older CPUs.

## Troubleshooting

| Problem | Fix |
|---|---|
| The other PC doesn't appear | Make sure hosting is on there, `firewall.bat` was run on both PCs, and both networks are Private. Try **+ Add by IP**. |
| "Could not reach the other PC" | Check that Zap Drop is running there and that the firewall rule exists. |
| "Certificate changed" | Zap Drop was reinstalled on the other PC. Remove the device and add it again. |
| A port is already in use | Run `run.bat --port 9000`. Then use `IP:9000` when adding the device, and adjust the firewall rule. |

## Development

```
zapdrop/
  __main__.py   start-up: two servers (HTTP 8765 + HTTPS 8766), discovery, engine
  server.py     peer API (/peer/*, PC to PC) and UI API (/api/*, browser to PC)
  engine.py     transfer engine: chunking, batching, parallel workers, resume, conflicts
  peers.py      known devices and our tokens for them
  discovery.py  UDP broadcast announcements
  fsutil.py     safe path handling, listings, manifests
  security.py   PIN hashing, issued tokens, rate limiting, TLS certificate
  db.py         SQLite: jobs, resume state, history
  web/          the UI (plain HTML/CSS/JS, no build step)
data/           settings, history and resume state (created on first run)
```

To run two instances on one PC for testing:

```
.venv\Scripts\python -m zapdrop --port 8765 --data data\A
.venv\Scripts\python -m zapdrop --port 9765 --data data\B
```

Then add `127.0.0.1:9765` as a device in the first instance.

### Packaging as an .exe (later)

```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --onefile --name ZapDrop --add-data "zapdrop\web;zapdrop\web" ZapDrop.py
```

When packaged, settings live in `%APPDATA%\Zap Drop`.
