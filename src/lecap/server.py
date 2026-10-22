"""`lecap open <folder>` and `lecap settings`: a small local server (127.0.0.1 only, offline).

It serves the lesson folder (with HTTP range requests so the video seeks), keeps notes and the Ask
history in <folder>/notes/, edits the global settings, and answers Ask questions.

Security: binds to 127.0.0.1; rejects foreign Host headers (DNS rebinding) and foreign Origins; API calls
need the X-Lecap header, which other websites can't send without a CORS approval this server never gives.
"""

import argparse
import json
import mimetypes
import posixpath
import re
import socket
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from . import APP_NAME, config, ui
from .ui import LecapError

BASE_PORT = 8476
NOTES = "notes"
API_HEADER = "X-Lecap"
MAX_BODY = 2_000_000


def atomic_write(path, text):
    """Write via a temp file + rename, so cloud sync never picks up a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def clean_notes(data):
    """Only the known shapes: {stars: {id: true}, notes: {id: text}, speakers: {id: name}, title?: text}."""
    if not isinstance(data, dict):
        raise ValueError("notes must be an object")
    out = {}
    for key, kind in (("stars", bool), ("notes", str), ("speakers", str)):
        part = data.get(key) or {}
        if not isinstance(part, dict):
            raise ValueError(f"{key} must be an object")
        out[key] = {str(k)[:12]: (v if kind is bool else str(v)[:20000]) for k, v in part.items()
                    if isinstance(v, kind) and (kind is not bool or v)}
    if isinstance(data.get("title"), str) and data["title"].strip():
        out["title"] = data["title"].strip()[:200]  # the lesson renamed on the page
    return out


class App:
    def __init__(self, folder=None):
        self.folder = Path(folder).resolve() if folder else None
        self.lock = threading.Lock()
        self._lesson = (None, None)

    def lesson(self):
        path = self.folder / "data" / "lesson.json"
        mtime = path.stat().st_mtime
        if self._lesson[0] != mtime:
            self._lesson = (mtime, json.loads(path.read_text(encoding="utf-8")))
        return self._lesson[1]

    @property
    def notes_path(self):
        return self.folder / NOTES / "notes.json"

    @property
    def chat_path(self):
        return self.folder / NOTES / "chat.json"


def providers_status():
    from .ai.registry import PROVIDERS, is_online

    out = []
    for name, p in PROVIDERS.items():
        ok, why = p.available()
        out.append({"name": name, "label": p.label, "online": is_online(p), "available": ok, "why": why,
                    "detail": p.describe() if ok and name == "local" else ""})
    return out


def settings_payload():
    from .config import CHOICES, DEFAULTS, HELP, KEY_ENV, SUGGESTIONS, get_secret, mask
    from .setup_ai import status

    keys = {}
    for provider in KEY_ENV:
        value, source = get_secret(provider)
        keys[provider] = {"set": bool(value), "masked": mask(value) if value else "", "source": source or ""}
    return {"values": config.load(), "defaults": DEFAULTS, "choices": CHOICES, "help": HELP, "keys": keys,
            "suggestions": SUGGESTIONS,
            "local": status(), "providers": providers_status(), "config_path": str(config.config_path())}


ONLINE_MODELS = {  # what the Ask box offers for Claude; local models come from LM Studio / Ollama
    "claude-code": [("opus", "Claude Code · Opus"), ("sonnet", "Claude Code · Sonnet")],
    "claude": [("claude-opus-5-5", "Claude API · Opus 5.5"), ("claude-sonnet-5-5", "Claude API · Sonnet 5.5")],
}


def model_options():
    """Everything the Ask box's model picker can offer, with what is available right now."""
    from .ai.registry import PROVIDERS
    from .setup_ai import status

    cfg = config.load()
    opts = [{"provider": "auto", "model": "", "label": "Automatic (local first)", "online": False, "available": True}]
    for a in status()["apps"]:
        if not a["running"]:
            continue
        names = list(dict.fromkeys(a["loaded"] + [m["name"] for m in a["models"]]))
        opts += [{"provider": "local", "model": n, "label": f"{n} · {a['name']}", "online": False, "available": True}
                 for n in names]
    for name, models in ONLINE_MODELS.items():
        ok = PROVIDERS[name].available()[0]
        opts += [{"provider": name, "model": m, "label": label, "online": True, "available": ok} for m, label in models]
    return {"options": opts, "current": {"provider": cfg["ask_provider"], "model": cfg["ask_model"]}}


def ask_payload(app, body):
    from . import ask
    from .ai.registry import PROVIDERS, is_online, pick

    question = str(body.get("question", "")).strip()[:2000]
    if not question:
        raise ValueError("empty question")
    mode = body.get("mode", "lesson")
    if mode not in ("lesson", "general", "web", "search"):
        raise ValueError("unknown mode")
    t = body.get("t")
    t = float(t) if isinstance(t, (int, float)) else None
    cfg = config.load()
    lesson = app.lesson()

    provider = None
    if mode == "search":
        pass
    elif mode == "lesson":
        provider = pick(cfg["ask_provider"])
        if is_online(provider):
            if cfg["allow_online"] == "off":
                provider = None  # offline search only
            elif cfg["allow_online"] == "ask" and not body.get("confirm_online"):
                return {"needs_confirm": True, "provider": provider.name, "label": provider.label}
    else:  # general knowledge or web: always an online model, always started by a click in the page
        if cfg["allow_online"] == "off":
            raise PermissionError("online answers are turned off in the settings")
        if mode == "web" and cfg["allow_web"] == "off":
            raise PermissionError("web search is turned off in the settings")
        name = cfg["ask_provider"] if cfg["ask_provider"] in ("claude", "claude-code") else (
            "claude" if config.get_secret("claude")[0] else "claude-code")
        provider = PROVIDERS[name]
    if provider is not None:
        ok, why = provider.available()
        if not ok:
            if mode == "lesson":
                provider = None
            else:
                raise LecapError(why)
    # The model picked in the Ask box (ask_model) belongs to the provider picked there (ask_provider)
    model = (cfg["ask_model"] if provider and provider.name == cfg["ask_provider"] else "") or (
        provider.default_model if provider else "")
    result = ask.answer(lesson, question, provider, model, t, "lesson" if mode == "search" else mode)
    result["mode"] = mode
    result["online"] = bool(provider and is_online(provider))
    entry = {"q": question, **{k: result.get(k) for k in ("answer", "main_time", "sources", "in_lesson", "provider",
                                                           "model", "mode", "online")}}
    with app.lock:
        chat = read_json(app.chat_path, [])
        chat = (chat + [entry])[-200:]
        atomic_write(app.chat_path, json.dumps(chat, ensure_ascii=False, indent=1))
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}"
    app: App = None

    def log_message(self, *args):  # quiet; errors are reported as JSON
        pass

    # --- guards -------------------------------------------------------------

    def _origin_ok(self):
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urllib.parse.urlparse(origin).netloc in allowed

    def _guard(self, api):
        if not self._origin_ok():
            self._json({"error": "forbidden"}, 403)
            return False
        if api and self.headers.get(API_HEADER) != "1":
            self._json({"error": "missing header"}, 403)
            return False
        return True

    # --- responses ----------------------------------------------------------

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path):
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            elif m.group(2):  # suffix range: the last N bytes
                start = max(size - int(m.group(2)), 0)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser cancelled (seeking)

    def _static(self, urlpath):
        if urlpath in ("/", "/settings") and (self.app.folder is None or urlpath == "/settings"):
            from .steps.render import icon_uri

            page = files("lecap").joinpath("web/settings.html").read_text(encoding="utf-8").replace("__ICON__", icon_uri()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if self.app.folder is None:
            return self._json({"error": "not found"}, 404)
        rel = posixpath.normpath(urllib.parse.unquote(urlpath)).lstrip("/") or "index.html"
        if rel.startswith("..") or "/../" in f"/{rel}/" or rel.split("/")[0] in ("notes",):
            return self._json({"error": "not found"}, 404)
        path = self.app.folder / rel  # symlinks inside the folder (e.g. --link media) are followed
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        self._file(path)

    # --- routing ------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        api = url.path.startswith("/api/")
        if not self._guard(api):
            return
        if not api:
            return self._static(url.path)
        self._api("GET", url.path)

    def do_PUT(self):
        self._api_entry("PUT")

    def do_POST(self):
        self._api_entry("POST")

    def do_DELETE(self):
        self._api_entry("DELETE")

    def do_OPTIONS(self):  # never approve cross-origin requests
        self.send_response(403)
        self.end_headers()

    def _api_entry(self, method):
        url = urllib.parse.urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        if self._guard(True):
            self._api(method, url.path)

    def _api(self, method, path):
        app = self.app
        try:
            if path == "/api/who" and method == "GET":  # lets a second `lecap open` reuse this server
                return self._json({"app": APP_NAME, "folder": str(app.folder) if app.folder else None})
            if path == "/api/state" and method == "GET":
                data = {"settings": config.load(), "providers": providers_status(), "server": True}
                if app.folder:
                    data["notes"] = read_json(app.notes_path, {})
                    data["chat"] = read_json(app.chat_path, [])
                return self._json(data)
            if path == "/api/notes" and method == "PUT" and app.folder:
                notes = clean_notes(self._body())
                with app.lock:
                    atomic_write(app.notes_path, json.dumps(notes, ensure_ascii=False, indent=1))
                return self._json({"ok": True})
            if path == "/api/chat" and method == "DELETE" and app.folder:
                with app.lock:
                    atomic_write(app.chat_path, "[]")
                return self._json({"ok": True})
            if path == "/api/models" and method == "GET":
                return self._json(model_options())
            if path == "/api/settings" and method == "GET":
                return self._json(settings_payload())
            if path == "/api/settings" and method == "PUT":
                cfg = config.load()
                for k, v in self._body().items():
                    cfg[k] = config.coerce(k, v)
                config.save(cfg)
                return self._json(settings_payload())
            if path.startswith("/api/keys/") and method in ("POST", "DELETE"):
                provider = path.rsplit("/", 1)[-1]
                if provider not in config.KEY_ENV:
                    return self._json({"error": "unknown provider"}, 404)
                if method == "DELETE":
                    config.delete_secret(provider)
                else:
                    key = str(self._body().get("key", "")).strip()
                    if not key:
                        raise ValueError("empty key")
                    config.set_secret(provider, key)
                return self._json(settings_payload())  # masked only, never the key itself
            if path == "/api/ask" and method == "POST" and app.folder:
                return self._json(ask_payload(app, self._body()))
            return self._json({"error": "not found"}, 404)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except (ValueError, KeyError) as e:
            return self._json({"error": str(e)}, 400)
        except LecapError as e:
            return self._json({"error": str(e)}, 502)


def free_port(start=BASE_PORT, tries=50):
    for port in range(start, start + tries):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def make_server(folder=None, port=None):
    app = App(folder)
    handler = type("LecapHandler", (Handler,), {"app": app})
    try:
        return ThreadingHTTPServer(("127.0.0.1", port if port is not None else free_port()), handler)
    except OSError:
        raise LecapError(f"port {port} is already in use (another `{APP_NAME} open` or `{APP_NAME} settings`?). "
                         "Leave out --port to use the next free one.")


def running_url(folder, start=BASE_PORT, tries=50):
    """The page URL of a `lecap open` already serving this folder, if there is one."""
    import urllib.request

    for port in range(start, start + tries):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/who", headers={API_HEADER: "1"})
        try:
            with urllib.request.urlopen(req, timeout=0.5) as r:
                if json.load(r).get("folder") == str(folder):
                    return f"http://127.0.0.1:{port}/index.html"
        except Exception:
            continue
    return None


def serve(folder=None, port=None, browser=True):
    if folder is not None:
        folder = Path(folder).expanduser().resolve()
        if not (folder / "index.html").exists() or not (folder / "data" / "lesson.json").exists():
            raise LecapError(f"{folder} is not a {APP_NAME} lesson folder (no index.html / data/lesson.json)")
        url = running_url(folder) if port is None else None
        if url:
            ui.info(f"already open: {url}")
            if browser:
                subprocess.run(["open", url])
            return 0
    httpd = make_server(folder, port)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/" + ("index.html" if folder else "")
    ui.title(f"{APP_NAME} is serving {'the lesson ' + str(folder) if folder else 'the settings'}")
    ui.info(f"open: {url}")
    ui.explain("Only this Mac can reach it. Notes are saved in the lesson's notes/ folder. Ctrl+C to stop.")
    if browser:
        subprocess.run(["open", url])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        ui.info("stopped")
    finally:
        httpd.server_close()
    return 0


def folder_from_url(url):
    """lecap://open?folder=/path/to/lesson → Path (the "Open in lecap" button of a page opened as a file)."""
    u = urllib.parse.urlparse(url)
    folder = urllib.parse.parse_qs(u.query).get("folder", [""])[0]
    if u.scheme != APP_NAME or u.netloc != "open" or not folder.startswith("/"):
        raise LecapError(f"not a {APP_NAME} link: {url}")
    return Path(folder)


def open_url(url):
    """Started by the Mac app's link helper, without a terminal: report problems as a notification."""
    try:
        return serve(folder_from_url(url))
    except LecapError as e:
        ui.notify(f"{APP_NAME}: can't open the lesson", str(e), sound="Basso")
        raise


def serve_main(argv):
    if argv[0] == "open-url":
        return open_url(argv[1] if len(argv) > 1 else "")
    p = argparse.ArgumentParser(prog=f"{APP_NAME} {argv[0]}")
    if argv[0] == "open":
        p.description = "Open a lesson page with notes saved in its folder, settings and Ask."
        p.add_argument("folder", type=Path, help="the lesson output folder (with index.html)")
    else:
        p.description = "Open the global settings page."
    p.add_argument("--port", type=int, help=f"port (default: the first free one from {BASE_PORT})")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    a = p.parse_args(argv[1:])
    return serve(getattr(a, "folder", None), a.port, not a.no_browser)
