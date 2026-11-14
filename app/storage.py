"""Optional free-space readout for folders mounted under STORAGE_DIR (e.g. /mnt/user -> /storage/Array)."""
import os

from . import config

NAMES = {"user": "Unraid shares", "user0": "Array", "cache": "Cache"}


def get() -> list[dict]:
    root = config.STORAGE_DIR
    if not root or not os.path.isdir(root):
        return []
    try:
        names = sorted(n for n in os.listdir(root) if not n.startswith("."))
    except OSError:
        return []
    paths = [os.path.join(root, n) for n in names if os.path.isdir(os.path.join(root, n))]
    if not paths and os.path.ismount(root):
        paths = [root]
    out, seen = [], set()
    for p in paths:
        try:
            st = os.statvfs(p)
            dev = os.stat(p).st_dev
        except OSError:
            continue
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        if not total or dev in seen:
            continue
        seen.add(dev)
        base = os.path.basename(p.rstrip("/")) or p
        out.append({"name": NAMES.get(base.lower(), base[:1].upper() + base[1:]), "total": total, "free": free,
                    "used": total - free})
    return out
