"""Place the input file into <out>/media/ so index.html can use a relative path."""

import os
import shutil


def place(src, media_dir, mode):
    """Copy, symlink or move src into media_dir. Returns (dest, what_happened)."""
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / src.name
    if dest.resolve() == src.resolve():
        return dest, "already in place"
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or dest.stat().st_size == src.stat().st_size:
            return dest, "already in place"
        dest.unlink()
    if mode == "link":
        os.symlink(src, dest)
        return dest, "symlinked"
    if mode == "move":
        shutil.move(src, dest)
        return dest, "moved"
    shutil.copy2(src, dest)
    return dest, "copied"
