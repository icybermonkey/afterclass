"""`afterclass app`: a native window to start a new lesson (drop the video and slides, pick a folder, Run).

The window is pywebview (WebKit). It runs the normal command line pipeline in a subprocess and
streams its output, so the app and `afterclass <video> <out>` always behave the same. Lessons and the
settings open as windows of the same app (File menu, "Open lesson", afterclass:// links).
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

from . import APP_NAME, DISPLAY_NAME, config, ui
from .server import folder_from_url, lesson_folder, running_url, start_in_background
from .ui import AfterclassError

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
        self._windows = None  # Windows: opens lessons and settings
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
            self._windows.show(self._out)
        return True

    def reveal(self):
        if self._out:
            subprocess.run(["open", str(self._out)])
        return True

    def settings(self):
        self._windows.show(None)
        return True


class PageApi:
    """window.pywebview.api.* of a lesson or settings window: what the browser would do with window.open."""

    def __init__(self, windows):
        self._windows = windows

    def settings(self):
        self._windows.show(None)
        return True

    def open_external(self, url):
        if str(url).startswith(("http://", "https://")):
            subprocess.run(["open", url])
        return True


class Windows:
    """The app's windows: the new-lesson window, one per lesson and settings, all in this process.

    A lesson or the settings get their own local server on a thread (the same one `afterclass open`
    runs), which stops when the window closes. The app quits when the last window closes.
    """

    def __init__(self, api, webview=None):
        if webview is None:
            import webview
        self.webview = webview
        self.api = api
        api._windows = self
        self.main = None
        self.main_shown = False
        self.pages = {}  # folder (or None: settings) -> (window, server or None)
        self.lock = threading.Lock()

    def new_lesson(self):
        if self.main is None:
            self.main = self.webview.create_window(f"{DISPLAY_NAME} · new lesson", html=_page(), js_api=self.api,
                                                   width=860, height=780, min_size=(680, 600),
                                                   background_color="#151517", text_select=True,
                                                   hidden=not self.main_shown)
            self.api._window = self.main
            self.main.events.loaded += lambda: _bind_drop(self.main, self.api)
            self.main.events.closed += self._main_closed
        if self.main_shown:
            self.main.show()
        return self.main

    def show_main(self):
        self.main_shown = True
        self.new_lesson()

    def _main_closed(self):
        self.main, self.main_shown = None, False

    def show(self, folder):
        """Open (or bring to the front) the window of a lesson folder, or the settings for None."""
        try:
            key = lesson_folder(folder) if folder is not None else None
        except AfterclassError as e:
            ui.notify(f"{DISPLAY_NAME}: can't open the lesson", str(e), sound="Basso")
            return None
        with self.lock:
            if key in self.pages:
                self.pages[key][0].show()
                return self.pages[key][0]
            httpd, url = None, running_url(key) if key else None  # a terminal `afterclass open` serves it already
            if not url:
                httpd, url = start_in_background(key)
            window = self.webview.create_window(_title(key), url, js_api=PageApi(self), width=1280, height=820,
                                                min_size=(720, 480), background_color="#151517", text_select=True)
            self.pages[key] = (window, httpd)
        window.events.closed += lambda: self._page_closed(key)
        return window

    def _page_closed(self, key):
        with self.lock:
            _, httpd = self.pages.pop(key, (None, None))
            last = not self.pages
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        if last and self.main is not None and not self.main_shown:
            # started by a link: the hidden new-lesson window would keep the app running unseen
            if self.api._state == "running":
                self.show_main()
            else:
                self.main.destroy()

    def choose_lesson(self):
        window = self.main if self.main_shown else next((w for w, _ in self.pages.values()), self.main)
        if window is None:
            return
        res = window.create_file_dialog(self.webview.FileDialog.FOLDER)
        if res:
            self.show(res[0])

    def open_url(self, url):
        try:
            folder = folder_from_url(url)
        except AfterclassError as e:
            ui.notify(f"{DISPLAY_NAME}: can't open the lesson", str(e), sound="Basso")
            return
        self.show(folder)

    def menu(self):
        from webview.menu import Menu, MenuAction, MenuSeparator

        def later(fn, *args):  # menu actions must not block the UI thread
            return lambda: threading.Thread(target=fn, args=args, daemon=True).start()

        return [Menu("File", [MenuAction("New Lesson", later(self.show_main)),
                              MenuAction("Open Lesson…", later(self.choose_lesson)),
                              MenuSeparator(),
                              MenuAction("Settings", later(self.show, None))])]

    def listen_for_links(self):
        """afterclass:// links (Info.plist CFBundleURLTypes) arrive as Apple Events: open them here."""
        try:
            import objc
            from Foundation import NSAppleEventManager, NSObject
        except ImportError:
            return
        windows = self

        class LinkHandler(NSObject):
            @objc.signature(b"v@:@@")
            def handle_withReply_(self, event, reply):
                url = event.paramDescriptorForKeyword_(DIRECT_OBJECT).stringValue()
                threading.Thread(target=windows.open_url, args=(str(url),), daemon=True).start()

        self._link_handler = LinkHandler.alloc().init()
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            self._link_handler, "handle:withReply:", GET_URL, GET_URL)

    def startup(self):
        """Show the new-lesson window, unless the app was started by a link (then only that lesson)."""
        time.sleep(0.6)
        with self.lock:
            opened = bool(self.pages)
        if not opened:
            self.show_main()


GET_URL = int.from_bytes(b"GURL", "big")  # kInternetEventClass / kAEGetURL
DIRECT_OBJECT = int.from_bytes(b"----", "big")  # keyDirectObject


def _title(folder):
    if folder is None:
        return f"{DISPLAY_NAME} · settings"
    try:
        return json.loads((folder / "data" / "lesson.json").read_text(encoding="utf-8")).get("title") or folder.name
    except (OSError, ValueError):
        return folder.name


def _quote(s):
    return f"'{s}'" if any(c in s for c in " '\"$&;") else s


def _page():
    from .steps.render import icon_uri

    return files(APP_NAME).joinpath("web/new.html").read_text(encoding="utf-8").replace("__ICON__", icon_uri())


def _brand_app():
    """Dock icon and menu name 'AfterClass' instead of Python (best effort, macOS only)."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = DISPLAY_NAME
        icns = str(files(APP_NAME).joinpath("assets/afterclass.icns"))
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icns))
    except Exception:
        pass


def _bind_drop(window, api):
    """Files dropped on the new-lesson window: their full paths go to the form."""
    from webview.dom import DOMEventHandler

    def on_drop(event):
        for f in event.get("dataTransfer", {}).get("files", []):
            path = f.get("pywebviewFullPath")
            if path:
                info = api.describe(path)
                window.evaluate_js(f"window.afterclassDropped({json.dumps(info)})")

    doc = window.dom.document
    ignore = DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True)
    doc.events.dragenter += ignore
    doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True, debounce=300)
    doc.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)


def main(argv=None):
    try:
        import webview  # noqa: F401
    except ImportError:
        print(f"{APP_NAME} app needs pywebview: uv tool install --reinstall --editable <repo>", file=sys.stderr)
        return 1

    api = Api()
    windows = Windows(api)
    windows.new_lesson()  # hidden until startup() knows the app wasn't started by a link
    windows.listen_for_links()
    _brand_app()
    # private_mode off: the pages keep their localStorage (view, theme…) between starts
    windows.webview.start(windows.startup, menu=windows.menu(), private_mode=False)
    if api._proc and api._state == "running":  # app quit while running: stop the job too
        api._proc.terminate()
    return 0
