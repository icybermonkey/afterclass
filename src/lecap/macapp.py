"""`lecap install-app`: put lecap.app into /Applications (or ~/Applications).

The bundle is tiny: an icon, an Info.plist and a launcher script that runs `lecap app`. Apps started
from Finder don't get the shell's PATH, so the launcher adds the usual Homebrew/uv locations
(ffmpeg, tesseract, lms, ollama and claude live there).
"""

import os
import plistlib
import shutil
import sys
from importlib.resources import files
from pathlib import Path

from . import APP_NAME, __version__, ui

BUNDLE_ID = f"ch.{APP_NAME}.app"
EXTRA_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "$HOME/.local/bin", "$HOME/.lmstudio/bin"]


def target_dir():
    system = Path("/Applications")
    return system if os.access(system, os.W_OK) else Path.home() / "Applications"


def lecap_command():
    """The installed command to launch (the uv tool), else this Python running lecap's CLI."""
    found = shutil.which(APP_NAME)
    return [found] if found else [sys.executable, "-m", f"{APP_NAME}.cli"]


def launcher(command):
    path = ":".join(EXTRA_PATH)
    cmd = " ".join(f'"{c}"' for c in command)
    return f"""#!/bin/sh
# {APP_NAME}.app launcher: Finder starts apps without the shell PATH, so add the usual tool locations
export PATH="{path}:$PATH"
exec {cmd} app
"""


def build(app_dir, command=None):
    """Create (or replace) the bundle at app_dir. Returns app_dir."""
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    exe = contents / "MacOS" / APP_NAME
    exe.write_text(launcher(command or lecap_command()))
    exe.chmod(0o755)
    shutil.copy(files(APP_NAME).joinpath("assets/lecap.icns"), contents / "Resources" / f"{APP_NAME}.icns")
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    return app_dir


def main(argv):
    remove = "--remove" in argv
    for base in (Path("/Applications"), Path.home() / "Applications"):
        app = base / f"{APP_NAME}.app"
        if remove and app.exists():
            shutil.rmtree(app)
            ui.ok(f"removed {app}")
    if remove:
        return 0
    app = build(target_dir() / f"{APP_NAME}.app")
    os.system(f"touch '{app}'")  # make Finder pick up the icon
    ui.ok(f"installed {app}")
    ui.info(f"Open it from Launchpad/Spotlight (\"{APP_NAME}\") or Finder. It runs: {' '.join(lecap_command())} app")
    ui.info(f"Remove it with: {APP_NAME} install-app --remove")
    return 0
