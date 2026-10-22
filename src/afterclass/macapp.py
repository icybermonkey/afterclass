"""`afterclass install-app`: put afterclass.app into /Applications (or ~/Applications).

The bundle is tiny: an icon, an Info.plist and a launcher script that runs `afterclass app`. Apps started
from Finder don't get the shell's PATH, so the launcher adds the usual Homebrew/uv locations
(ffmpeg, tesseract, lms, ollama and claude live there).

Inside it sits a hidden helper app for `afterclass://open?folder=…` links (the "Open in afterclass" button of a
page opened as a file). Links arrive as Apple Events, which a shell script can't receive, so the helper
is a small AppleScript applet that hands the link to `afterclass open-url`.
"""

import os
import plistlib
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from . import APP_NAME, OLD_NAME, __version__, ui

BUNDLE_ID = f"ch.{APP_NAME}.app"
SCHEME = APP_NAME  # afterclass://open?folder=/path/to/lesson
HELPER = f"Contents/Helpers/Open in {APP_NAME}.app"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")
HELPER_SCRIPT = """on open location theURL
	do shell script quoted form of (POSIX path of (path to me) & "Contents/Resources/open-url") & " " & quoted form of theURL & " > /dev/null 2>&1 &"
end open location
"""
EXTRA_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "$HOME/.local/bin", "$HOME/.lmstudio/bin"]


def target_dir():
    system = Path("/Applications")
    return system if os.access(system, os.W_OK) else Path.home() / "Applications"


def afterclass_command():
    """The installed command to launch (the uv tool), else this Python running afterclass's CLI."""
    found = shutil.which(APP_NAME)
    return [found] if found else [sys.executable, "-m", f"{APP_NAME}.cli"]


def launcher(command, args="app"):
    path = ":".join(EXTRA_PATH)
    cmd = " ".join(f'"{c}"' for c in command)
    return f"""#!/bin/sh
# {APP_NAME}.app launcher: Finder starts apps without the shell PATH, so add the usual tool locations
export PATH="{path}:$PATH"
exec {cmd} {args}
"""


def build(app_dir, command=None):
    """Create (or replace) the bundle at app_dir. Returns app_dir."""
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    exe = contents / "MacOS" / APP_NAME
    exe.write_text(launcher(command or afterclass_command()))
    exe.chmod(0o755)
    shutil.copy(files(APP_NAME).joinpath("assets/afterclass.icns"), contents / "Resources" / f"{APP_NAME}.icns")
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
    if sys.platform == "darwin":
        build_helper(app_dir / HELPER, command or afterclass_command())
    return app_dir


def build_helper(helper, command):
    """The background applet that opens afterclass:// links (needs macOS: osacompile, codesign)."""
    helper.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["osacompile", "-o", str(helper), "-e", HELPER_SCRIPT], check=True, capture_output=True)
    res = helper / "Contents" / "Resources"
    script = res / "open-url"
    script.write_text(launcher(command, 'open-url "$1"'))
    script.chmod(0o755)
    shutil.copy(files(APP_NAME).joinpath("assets/afterclass.icns"), res / "applet.icns")
    plist = helper / "Contents" / "Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info.update({
        "CFBundleIdentifier": BUNDLE_ID + ".opener",
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "LSBackgroundOnly": True,  # no Dock icon, no menu bar
        "CFBundleURLTypes": [{"CFBundleURLName": f"{APP_NAME} lesson", "CFBundleURLSchemes": [SCHEME, OLD_NAME]}],
    })
    plist.write_bytes(plistlib.dumps(info))
    # Editing the plist breaks the applet's signature; Apple silicon refuses unsigned code, so sign it again
    subprocess.run(["codesign", "--force", "--sign", "-", str(helper)], check=True, capture_output=True)
    return helper


def register(helper, on=True):
    """Register (or unregister) the helper with Launch Services, so afterclass:// links reach it."""
    if Path(LSREGISTER).exists() and helper.exists():
        subprocess.run([LSREGISTER, "-f" if on else "-u", str(helper)], capture_output=True)


def main(argv):
    remove = "--remove" in argv
    for base in (Path("/Applications"), Path.home() / "Applications"):
        app = base / f"{APP_NAME}.app"
        if remove and app.exists():
            register(app / HELPER, on=False)
            shutil.rmtree(app)
            ui.ok(f"removed {app}")
    if remove:
        return 0
    app = target_dir() / f"{APP_NAME}.app"
    register(app / HELPER, on=False)
    build(app)
    register(app / HELPER)
    os.system(f"touch '{app}'")  # make Finder pick up the icon
    ui.ok(f"installed {app}")
    ui.info(f"Lesson pages opened as a file get an \"Open in {APP_NAME}\" button ({SCHEME}:// links).")
    ui.info(f"Open it from Launchpad/Spotlight (\"{APP_NAME}\") or Finder. It runs: {' '.join(afterclass_command())} app")
    ui.info(f"Remove it with: {APP_NAME} install-app --remove")
    return 0
