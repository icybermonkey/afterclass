"""`afterclass install-app`: put AfterClass.app into /Applications (or ~/Applications).

The bundle's executable is a small native launcher (assets/launcher.c, compiled here with clang) that runs
`afterclass app` with Python embedded in its own process. So the Dock shows "AfterClass" with its icon, not
"python3.14", and every window (new lesson, lessons, settings) belongs to that one app. The launcher links the
tool's Python: after a Python upgrade (`uv tool install --reinstall …`) run `afterclass install-app` again.
Without clang (Xcode command line tools) the executable is a launcher script instead, which works but shows
up as Python.

Apps started from Finder don't get the shell's PATH, so the launcher adds the usual Homebrew/uv locations
(ffmpeg, tesseract, lms, ollama and claude live there).

The app also handles `afterclass://open?folder=…` links (the "Open in afterclass" button of a page opened as
a file): they arrive as Apple Events, which gui.py turns into a lesson window.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from . import APP_NAME, DISPLAY_NAME, OLD_NAME, __version__, ui

BUNDLE_ID = f"ch.{APP_NAME}.app"
SCHEME = APP_NAME  # afterclass://open?folder=/path/to/lesson
OLD_HELPER = f"Contents/Helpers/Open in {APP_NAME}.app"  # handled links before the app did itself
BUNDLES = (f"{DISPLAY_NAME}.app", f"{APP_NAME}.app")  # the app, and its name before AfterClass
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")
EXTRA_PATH = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "$HOME/.local/bin", "$HOME/.lmstudio/bin"]


def target_dir():
    system = Path("/Applications")
    return system if os.access(system, os.W_OK) else Path.home() / "Applications"


def afterclass_command():
    """The installed command to launch (the uv tool), else this Python running afterclass's CLI."""
    found = shutil.which(APP_NAME)
    return [found] if found else [sys.executable, "-m", f"{APP_NAME}.cli"]


def tool_python(command):
    """The Python that runs `command`: the shebang of the installed script, else this one."""
    if len(command) == 1:
        try:
            first = Path(command[0]).read_bytes()[:500].split(b"\n", 1)[0].decode()
        except (OSError, UnicodeDecodeError):
            first = ""
        if first.startswith("#!") and Path(first[2:].strip()).exists():
            return first[2:].strip()
    return sys.executable


def launcher(command, args="app"):
    path = ":".join(EXTRA_PATH)
    cmd = " ".join(f'"{c}"' for c in command)
    return f"""#!/bin/sh
# {APP_NAME}.app launcher: Finder starts apps without the shell PATH, so add the usual tool locations
export PATH="{path}:$PATH"
exec {cmd} {args}
"""


def compile_launcher(exe, python, arg="app"):
    """Compile assets/launcher.c into `exe`: runs `afterclass <arg>` in `python`'s environment. Returns exe."""
    query = "import sysconfig as s; print(s.get_config_var('INCLUDEPY')); print(s.get_config_var('LIBDIR')); print(s.get_config_var('VERSION'))"
    include, libdir, version = subprocess.run([python, "-c", query], capture_output=True, text=True,
                                              check=True).stdout.split()
    extra = ":".join(EXTRA_PATH).replace("$HOME", str(Path.home()))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "launcher.c"
        src.write_bytes(files(APP_NAME).joinpath("assets/launcher.c").read_bytes())
        subprocess.run(["clang", "-O2", str(src), f"-I{include}", f"-L{libdir}", f"-lpython{version}",
                        f"-Wl,-rpath,{libdir}", f'-DAFTERCLASS_PYTHON="{python}"', f'-DAFTERCLASS_ARG="{arg}"',
                        f'-DEXTRA_PATH="{extra}"', "-o", str(exe)], check=True, capture_output=True)
    return exe


def can_compile():
    return sys.platform == "darwin" and shutil.which("clang") is not None


def build(app_dir, command=None, python=None):
    """Create (or replace) the bundle at app_dir. With `python` (and clang) the executable is the native
    launcher, else a launcher script for `command`. Returns app_dir."""
    if app_dir.exists():
        shutil.rmtree(app_dir)
    contents = app_dir / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    exe = contents / "MacOS" / APP_NAME
    if python and can_compile():
        compile_launcher(exe, python)
    else:
        exe.write_text(launcher(command or afterclass_command()))
        exe.chmod(0o755)
    shutil.copy(files(APP_NAME).joinpath("assets/afterclass.icns"), contents / "Resources" / f"{APP_NAME}.icns")
    info = {
        "CFBundleName": DISPLAY_NAME,
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "CFBundleURLTypes": [{"CFBundleURLName": f"{DISPLAY_NAME} lesson", "CFBundleURLSchemes": [SCHEME, OLD_NAME]}],
    }
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    if sys.platform == "darwin":
        # Apple silicon refuses unsigned code: sign the whole bundle ad hoc
        subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, str(app_dir)],
                       check=True, capture_output=True)
    return app_dir


def register(app, on=True):
    """Register (or unregister) a bundle with Launch Services, so afterclass:// links reach it."""
    if Path(LSREGISTER).exists() and app.exists():
        subprocess.run([LSREGISTER, "-f" if on else "-u", str(app)], capture_output=True)


def main(argv):
    remove = "--remove" in argv
    for app in [base / name for base in (Path("/Applications"), Path.home() / "Applications") for name in BUNDLES]:
        if app.exists():
            register(app / OLD_HELPER, on=False)
            if remove:
                register(app, on=False)
                shutil.rmtree(app)
                ui.ok(f"removed {app}")
    if remove:
        return 0
    command = afterclass_command()
    python = tool_python(command)
    if not can_compile():
        ui.warn("clang not found: install the Xcode command line tools (xcode-select --install) and run this "
                f"again, so the Dock shows \"{DISPLAY_NAME}\" instead of Python. Using a launcher script for now.")
    app = target_dir() / f"{DISPLAY_NAME}.app"
    build(app, command, python)
    register(app)
    os.system(f"touch '{app}'")  # make Finder pick up the icon
    ui.ok(f"installed {app}")
    ui.info(f"Lesson pages opened as a file get an \"Open in {DISPLAY_NAME}\" button ({SCHEME}:// links).")
    ui.info(f"Open it from Launchpad/Spotlight (\"{DISPLAY_NAME}\") or Finder. It runs `{APP_NAME} app` with {python}")
    ui.info(f"After a Python upgrade, run `{APP_NAME} install-app` again. Remove it with: {APP_NAME} install-app --remove")
    return 0
