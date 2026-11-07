"""Filesystem helpers: safe path handling, directory listings and transfer manifests."""

import os
import re
import string
from pathlib import Path

_INVALID = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class PathError(Exception):
    pass


def list_drives() -> list[dict]:
    out = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                out.append({"name": f"{letter}:", "path": root, "is_dir": True, "size": 0, "mtime": 0})
    else:
        out.append({"name": "/", "path": "/", "is_dir": True, "size": 0, "mtime": 0})
    return out


def list_dir(path: str) -> list[dict]:
    """List a real directory. Folders first, then files, case-insensitive by name."""
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                is_dir = e.is_dir()
                st = e.stat()
                entries.append({"name": e.name, "path": e.path, "is_dir": is_dir,
                                "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime})
            except OSError:
                continue
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries


def split_vpath(vpath: str) -> list[str]:
    parts = [p for p in re.split(r"[\\/]+", vpath or "") if p not in ("", ".")]
    for p in parts:
        if p == ".." or ":" in p:
            raise PathError("Invalid path")
    return parts


def safe_join(root: str, parts: list[str]) -> str:
    root_abs = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root_abs, *parts)) if parts else root_abs
    if os.path.commonpath([root_abs, full]) != root_abs:
        raise PathError("Path escapes its folder")
    return full


def sanitize_rel(rel: str) -> list[str]:
    """Make a path that came from another PC safe to create on this one."""
    parts = []
    for p in re.split(r"[\\/]+", rel):
        if p in ("", "."):
            continue
        if p == "..":
            raise PathError("Invalid path from peer")
        p = _INVALID.sub("_", p).rstrip(" .") or "_"
        if p.split(".")[0].upper() in _RESERVED:
            p = "_" + p
        parts.append(p)
    if not parts:
        raise PathError("Empty path from peer")
    return parts


def free_name(path: Path) -> Path:
    """'file.txt' -> 'file (1).txt' etc. until unused."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    if path.is_dir():
        stem, suffix = path.name, ""
    i = 1
    while True:
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1


class VirtualSource:
    """A set of named top-level items (shared folders, or files/folders offered in a push).

    Virtual paths look like "<item name>/sub/dir/file.txt".
    """

    def __init__(self, items: dict[str, str]):
        self.items = items

    def resolve(self, vpath: str) -> str:
        parts = split_vpath(vpath)
        if not parts:
            raise PathError("Choose a shared item")
        top = self.items.get(parts[0])
        if top is None:
            raise PathError(f"'{parts[0]}' is not shared")
        if len(parts) > 1 and not os.path.isdir(top):
            raise PathError("Not a folder")
        return safe_join(top, parts[1:])

    def browse(self, vpath: str) -> list[dict]:
        parts = split_vpath(vpath)
        if not parts:
            out = []
            for name, p in self.items.items():
                try:
                    st = os.stat(p)
                    is_dir = os.path.isdir(p)
                    out.append({"name": name, "is_dir": is_dir, "size": 0 if is_dir else st.st_size,
                                "mtime": st.st_mtime})
                except OSError:
                    out.append({"name": name, "is_dir": True, "size": 0, "mtime": 0, "missing": True})
            return out
        real = self.resolve(vpath)
        return [{k: v for k, v in e.items() if k != "path"} for e in list_dir(real)]

    def manifest(self, vpaths: list[str]) -> dict:
        """Recursively list everything under the selected virtual paths.

        rel paths are relative to the parent of each selection, so selecting
        "Movies/2024" yields files like "2024/a.mkv".
        """
        files, dirs, errors = [], [], []
        seen_tops = set()
        for vp in vpaths:
            parts = split_vpath(vp)
            real = self.resolve(vp)
            base = parts[-1]
            while base in seen_tops:  # two selections with the same name
                base = base + "_"
            seen_tops.add(base)
            vp_norm = "/".join(parts)
            if os.path.isdir(real):
                dirs.append(base)
                self._walk(real, vp_norm, base, files, dirs, errors)
            elif os.path.isfile(real):
                st = os.stat(real)
                files.append({"src": vp_norm, "rel": base, "size": st.st_size, "mtime": st.st_mtime})
            else:
                errors.append(f"Not found: {vp}")
        return {"files": files, "dirs": dirs, "errors": errors,
                "total_bytes": sum(f["size"] for f in files), "total_files": len(files)}

    @staticmethod
    def _walk(real, vprefix, rprefix, files, dirs, errors):
        stack = [(real, vprefix, rprefix)]
        while stack:
            d, vp, rp = stack.pop()
            try:
                it = list(os.scandir(d))
            except OSError as e:
                errors.append(f"{rp}: {e.strerror or e}")
                continue
            it.sort(key=lambda e: e.name.lower())
            subdirs = []
            for e in it:
                try:
                    if e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction()):
                        continue
                    if e.is_dir():
                        subdirs.append(e)
                    elif e.is_file():
                        st = e.stat()
                        files.append({"src": f"{vp}/{e.name}", "rel": f"{rp}/{e.name}",
                                      "size": st.st_size, "mtime": st.st_mtime})
                except OSError as ex:
                    errors.append(f"{rp}/{e.name}: {ex.strerror or ex}")
            for e in reversed(subdirs):
                dirs.append(f"{rp}/{e.name}")
                stack.append((e.path, f"{vp}/{e.name}", f"{rp}/{e.name}"))
