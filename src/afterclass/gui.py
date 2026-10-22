"""`afterclass app`: a native window to start a new lesson (drop the video and slides, pick a folder, Run).

The window is pywebview (WebKit). It runs the normal command line pipeline in a subprocess and
streams its output, so the app and `afterclass <video> <out>` always behave the same.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from importlib.resources import files
from pathlib import Path

from . import APP_NAME, config

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
DECK_EXT = {".pdf", ".pptx", ".ppt", ".odp", ".key"}
STATE_FILE = "app.json"  # remembers the last output folder, next to config.toml
AI_CHOICES = ("off", "auto", "local", "claude-code", "claude")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "-", name or "").strip(" .") or "lesson"


def output_folder(form):
    """Chosen folder + lesson name (default: the video's file name)."""
    name = form.get("name") or Path(form.get("video") or "lesson").stem
    return Path(form["out_dir"]).expanduser() / safe_name(name)


def build_args(form):
    """The `afterclass` command line for the form. Returns (args, output folder)."""
    out = output_folder(form)
    args = [str(Path(form["video"]).expanduser()), str(out)]
    if form.get("deck"):
        args += ["--deck", str(Path(form["deck"]).expanduser())]
    if form.get("name"):
        args += ["--title", form["name"]]
    if form.get("language"):
        args += ["-l", form["language"]]
    ai = form.get("ai", "off")
    if ai not in AI_CHOICES:
        raise ValueError(f"unknown AI choice {ai}")
    if ai == "auto":
        args += ["--ai"]
    elif ai != "off":
        args += ["--ai", ai]
        if ai == "claude" and form.get("accept_cost"):
            args += ["-y"]  # the paid API only runs after this explicit checkbox
    if form.get("speakers") is False:
        args += ["--no-speakers"]
    if form.get("media") in ("copy", "link", "move"):
        args += ["--" + form["media"]]
    if form.get("deck") and form.get("deck_media") in ("copy", "link", "move"):
        args += ["--deck-mode", form["deck_media"]]
    return args, out


def _state_path():
    return config.config_path().parent / STATE_FILE


def _load_state():
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(**kw):
    state = {**_load_state(), **kw}
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def _kind(path):
    p = Path(path)
    if p.is_dir():
        return "folder"
    ext = p.suffix.lower()
    return "video" if ext in VIDEO_EXT else "deck" if ext in DECK_EXT else None


def _cli(*args):
    return [sys.executable, "-m", f"{APP_NAME}.cli", *args]


class Api:
    """Methods the page calls via window.pywebview.api.*"""

    def __init__(self):
        self._window = None
        self._proc = None
        self._lines = []
        self._state = "idle"  # idle | running | done | failed | cancelled
        self._out = None
        self._started = 0.0
        self._finished = 0.0

    # --- form -----------------------------------------------------------------

    def defaults(self):
        cfg = config.load()
        last = _load_state().get("out_dir")
        return {"language": cfg["language"], "ai": "auto", "speakers": bool(cfg["speakers"]), "media": cfg["media"], "deck_media": cfg["deck_media"],
                "out_dir": last if last and Path(last).is_dir() else str(Path.home() / "Documents"),
                "app": APP_NAME}

    def choose(self, kind):
        import webview

        if kind == "folder":
            res = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        else:
            types = (("Video (*.mp4;*.mov;*.m4v;*.mkv;*.webm;*.avi)",) if kind == "video"
                     else ("Slides (*.pdf;*.pptx;*.ppt;*.odp)",))
            res = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        return self.describe(res[0]) if res else None

    def describe(self, path):
        p = Path(path)
        size = p.stat().st_size if p.is_file() else 0
        return {"path": str(p), "name": p.name, "kind": _kind(p), "mb": round(size / 1e6)}

    def preview(self, form):
        if not form.get("out_dir"):
            return {"out": ""}
        out = output_folder(form)
        state = ("existing lesson: cached steps are reused" if (out / "data").is_dir()
                 else "not empty: choose another name" if out.exists() and any(out.iterdir()) else "new folder")
        return {"out": str(out), "state": state}

    # --- run --------------------------------------------------------------------

    def run(self, form):
        if self._state == "running":
            return {"error": "already running"}
        if not form.get("video") or not Path(form["video"]).is_file():
            return {"error": "choose the recording first"}
        if not form.get("out_dir") or not Path(form["out_dir"]).is_dir():
            return {"error": "choose an output folder"}
        try:
            args, out = build_args(form)
        except ValueError as e:
            return {"error": str(e)}
        _save_state(out_dir=form["out_dir"])
        self._lines, self._out, self._state = [], out, "running"
        self._started, self._finished = time.time(), 0.0
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"}
        self._proc = subprocess.Popen(_cli(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     bufsize=1, env=env, stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()
        return {"out": str(out), "command": f"{APP_NAME} " + " ".join(_quote(a) for a in args)}

    def _read(self):
        for line in self._proc.stdout:  # universal newlines: \r progress frames arrive as lines too
            self._lines.append(line.rstrip())
        code = self._proc.wait()
        if self._state == "running":
            self._state = "done" if code == 0 else "failed"
        self._finished = time.time()

    def poll(self, since=0):
        end = self._finished or time.time()
        return {"state": self._state, "lines": self._lines[since:], "next": len(self._lines),
                "elapsed": round(end - self._started) if self._started else 0, "out": str(self._out or "")}

    def cancel(self):
        if self._proc and self._state == "running":
            self._state = "cancelled"
            self._proc.terminate()
        return True

    def reset(self):
        if self._state != "running":
            self._lines, self._state, self._out, self._started = [], "idle", None, 0.0
        return True

    # --- after the run ---------------------------------------------------------------

    def open_lesson(self):
        if self._out:
            subprocess.Popen(_cli("open", str(self._out)), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def reveal(self):
        if self._out:
            subprocess.run(["open", str(self._out)])
        return True

    def settings(self):
        subprocess.Popen(_cli("settings"), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True


def _quote(s):
    return f"'{s}'" if any(c in s for c in " '\"$&;") else s


def _page():
    from .steps.render import icon_uri

    return files(APP_NAME).joinpath("web/new.html").read_text(encoding="utf-8").replace("__ICON__", icon_uri())


def _brand_app():
    """Dock icon and menu name 'afterclass' instead of Python (best effort, macOS only)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = APP_NAME
        icns = str(files(APP_NAME).joinpath("assets/afterclass.icns"))
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icns))
    except Exception:
        pass


def main(argv=None):
    try:
        import webview
        from webview.dom import DOMEventHandler
    except ImportError:
        print(f"{APP_NAME} app needs pywebview: uv tool install --reinstall --editable <repo>", file=sys.stderr)
        return 1

    api = Api()
    window = webview.create_window(f"{APP_NAME} · new lesson", html=_page(), js_api=api, width=860, height=780,
                                   min_size=(680, 600), background_color="#151517", text_select=True)
    api._window = window

    def on_drop(event):
        for f in event.get("dataTransfer", {}).get("files", []):
            path = f.get("pywebviewFullPath")
            if path:
                info = api.describe(path)
                window.evaluate_js(f"window.afterclassDropped({json.dumps(info)})")

    def bind(win):
        doc = win.dom.document
        ignore = DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True)
        doc.events.dragenter += ignore
        doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True, debounce=300)
        doc.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)

    _brand_app()
    webview.start(bind, window)
    if api._proc and api._state == "running":  # window closed while running: stop the job too
        api._proc.terminate()
    return 0
