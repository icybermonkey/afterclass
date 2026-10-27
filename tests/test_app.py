import plistlib
import subprocess
import sys

import pytest

from afterclass import gui, macapp


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    """app.json (recent lessons) goes to tmp, not the real config folder."""
    monkeypatch.setenv("AFTERCLASS_CONFIG", str(tmp_path / "config" / "config.toml"))


def form(**kw):
    return {"video": "/x/SPCS lesson 1.mp4", "out_dir": "/x/out", "language": "en", "ai": "auto", "speakers": True, **kw}


def test_form_becomes_the_normal_command_line():
    args, out = gui.build_args(form())
    assert str(out) == "/x/out/SPCS lesson 1"  # lesson name defaults to the video's file name
    assert args == ["/x/SPCS lesson 1.mp4", "/x/out/SPCS lesson 1", "-l", "en", "--ai"]

    args, out = gui.build_args(form(name="Lesson: 2/Obfuscation", deck="/x/s.pdf", ai="off", speakers=False))
    assert str(out) == "/x/out/Lesson- 2-Obfuscation"  # safe folder name
    assert args[2:] == ["--deck", "/x/s.pdf", "--title", "Lesson: 2/Obfuscation", "-l", "en", "--no-speakers"]

    assert gui.build_args(form(ai="claude"))[0][-2:] == ["--ai", "claude"]  # no cost confirmation: asks/skips
    assert gui.build_args(form(ai="claude", accept_cost=True))[0][-3:] == ["--ai", "claude", "-y"]
    assert gui.build_args(form(ai="claude-code"))[0][-2:] == ["--ai", "claude-code"]


def test_app_bundle(tmp_path):
    app = macapp.build(tmp_path / "afterclass.app", command=["/usr/local/bin/afterclass"])
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "afterclass" and info["CFBundleIconFile"] == "afterclass"
    exe = app / "Contents/MacOS/afterclass"
    text = exe.read_text()
    assert exe.stat().st_mode & 0o111 and '/opt/homebrew/bin' in text and text.rstrip().endswith('exec "/usr/local/bin/afterclass" app')
    assert (app / "Contents/Resources/afterclass.icns").stat().st_size > 1000


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is macOS only")
def test_app_handles_lesson_links(tmp_path):
    app = macapp.build(tmp_path / "afterclass.app", command=["/usr/local/bin/afterclass"])
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["afterclass", "lecap"]
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "AfterClass"  # the command stays afterclass
    assert not (app / macapp.OLD_HELPER).exists()
    assert subprocess.run(["codesign", "--verify", str(app)]).returncode == 0


@pytest.mark.skipif(not macapp.can_compile(), reason="the native launcher needs macOS and clang")
def test_native_launcher_runs_afterclass_in_this_python(tmp_path):
    exe = macapp.compile_launcher(tmp_path / "afterclass", sys.executable, arg="--help")
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("usage: afterclass")
    app = macapp.build(tmp_path / "afterclass.app", python=sys.executable)
    assert (app / "Contents/MacOS/afterclass").read_bytes()[:4] == bytes.fromhex("cffaedfe")  # Mach-O, not a script
    assert subprocess.run(["codesign", "--verify", str(app)]).returncode == 0


class FakeWindow:
    def __init__(self, title, url=None, **kw):
        self.title, self.url, self.kw, self.shown, self.destroyed = title, url, kw, 0, False

        class Events:
            def __init__(self):
                self.closed, self.loaded, self.shown = Hook(), Hook(), Hook()

        self.events = Events()

    def show(self):
        self.shown += 1

    def destroy(self):
        self.destroyed = True


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def fire(self):
        for fn in self:
            fn()


class FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, url=None, **kw):
        self.windows.append(FakeWindow(title, url, **kw))
        return self.windows[-1]


def lesson(tmp_path, name="L1"):
    folder = tmp_path / name
    (folder / "data").mkdir(parents=True)
    (folder / "index.html").write_text("<html></html>")
    (folder / "data/lesson.json").write_text('{"title": "Lesson one"}')
    return folder


def test_a_lesson_opens_once_in_its_own_window_with_a_server(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "running_url", lambda folder: None)
    web = FakeWebview()
    windows = gui.Windows(gui.Api(), webview=web)
    folder = lesson(tmp_path)
    first = windows.show(folder)
    assert first.title == "Lesson one" and first.url.startswith("http://127.0.0.1:") and first.url.endswith("/index.html")
    assert windows.show(folder) is first and first.shown == 1 and len(web.windows) == 1  # the same window again
    httpd = windows.pages[folder.resolve()][1]
    first.events.closed.fire()
    assert not windows.pages and httpd.socket.fileno() == -1  # its server stopped


def test_started_by_a_link_the_app_quits_with_the_lesson_window(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "running_url", lambda folder: None)
    web = FakeWebview()
    windows = gui.Windows(gui.Api(), webview=web)
    main = windows.new_lesson()
    assert main.kw["hidden"] is True
    page = windows.show(lesson(tmp_path))
    page.events.closed.fire()
    assert main.destroyed  # the hidden new-lesson window doesn't keep the app running unseen


def test_settings_window_and_bad_lesson_links(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(gui.ui, "notify", lambda title, msg, sound=None: notes.append(msg))
    web = FakeWebview()
    windows = gui.Windows(gui.Api(), webview=web)
    assert windows.show(None).url.endswith("/") and web.windows[0].title == "AfterClass · settings"
    windows.open_url(f"afterclass://open?folder={tmp_path}")  # not a lesson folder
    windows.open_url("https://example.com")
    assert len(notes) == 2 and len(web.windows) == 1
    web.windows[0].events.closed.fire()


def test_media_choice_is_passed_on():
    assert gui.build_args(form(media="move"))[0][-1] == "--move"
    assert gui.build_args(form(media="link"))[0][-1] == "--link"


def test_rerun_finds_files_that_were_moved_into_the_lesson(tmp_path):
    from pathlib import Path

    from afterclass import cli

    out = tmp_path / "lesson"
    (out / "media").mkdir(parents=True)
    (out / "media" / "talk.mp4").write_bytes(b"x")
    assert cli.reuse_moved(Path("/gone/talk.mp4"), out) == out / "media" / "talk.mp4"
    assert cli.reuse_moved(Path("/gone/other.mp4"), out) == Path("/gone/other.mp4")  # unknown: left for the error
    assert cli.reuse_moved(None, out) is None


def test_recording_and_slides_have_their_own_mode():
    args = gui.build_args(form(media="move", deck="/x/s.pdf", deck_media="copy"))[0]
    assert "--move" in args and args[-2:] == ["--deck-mode", "copy"]
    assert "--deck-mode" not in gui.build_args(form(deck_media="move"))[0]  # no slides: nothing to place


def test_notification_script(monkeypatch):
    import sys

    from afterclass import ui

    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    ui.notify("afterclass: lesson ready", 'SPCS "Lesson 1" is ready')
    assert calls[0][:2] == ["osascript", "-e"]
    assert calls[0][2] == 'display notification "SPCS \\"Lesson 1\\" is ready" with title "afterclass: lesson ready" sound name "Glass"'


def test_model_suggestions_fit_the_memory():
    from afterclass import setup_ai

    big = [n for n, _, _ in setup_ai.choices(32, "lmstudio")]
    assert big[0] == "google/gemma-4-26b-a4b-qat" and "qwen/qwen3.6-35b-a3b" in big
    assert [n for n, _, _ in setup_ai.choices(16, "lmstudio")][0] == "google/gemma-4-12b-qat"
    assert [n for n, _, _ in setup_ai.choices(8, "lmstudio")] == ["google/gemma-4-e4b-qat"]
    assert all(n for n, _, _ in setup_ai.choices(32, "ollama"))  # models without an Ollama tag are skipped
    assert setup_ai.recommend(32, "ollama")[0] == "gemma4:26b"


def test_recommendation_uses_this_mac():
    from afterclass import setup_ai

    m2pro = {"chip": "Apple M2 Pro", "ram_gb": 32, "free_gb": 36, "bandwidth": 200}
    offer = setup_ai.choices(32, "lmstudio", m2pro)
    assert "words/s on your M2 Pro" in offer[0][2]
    assert setup_ai.words_per_second(3.8, 200) > setup_ai.words_per_second(30.7, 200) * 5  # MoE is much faster
    assert setup_ai.recommend(32, "lmstudio", m2pro)[0] == "google/gemma-4-26b-a4b-qat"
    tight = {**m2pro, "free_gb": 12}  # not enough disk for the big ones: recommend one that fits
    assert setup_ai.recommend(32, "lmstudio", tight)[0] == "google/gemma-4-12b-qat"
    assert "⚠ only 12 GB free disk" in setup_ai.choices(32, "lmstudio", tight)[0][2]


def test_opened_lessons_are_remembered_most_recent_first(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "running_url", lambda folder: None)
    windows = gui.Windows(gui.Api(), webview=FakeWebview())
    a, b = lesson(tmp_path, "A"), lesson(tmp_path, "B")
    for folder in (a, b, a):
        windows.show(folder).events.closed.fire()
    assert [r["path"] for r in gui.recent_lessons()] == [str(a.resolve()), str(b.resolve())]
    (b / "index.html").unlink()  # gone: no longer listed
    assert [r["title"] for r in gui.Api().recent()] == ["Lesson one"]


def test_startup_opens_the_most_recent_lesson_else_the_new_lesson_window(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "running_url", lambda folder: None)
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    windows = gui.Windows(gui.Api(), webview=FakeWebview())
    windows.new_lesson()
    windows.startup()
    assert windows.main_shown and not windows.pages
    gui.remember_lesson(lesson(tmp_path).resolve())
    windows = gui.Windows(gui.Api(), webview=FakeWebview())
    windows.new_lesson()
    windows.startup()
    assert not windows.main_shown and list(windows.pages) == [(tmp_path / "L1").resolve()]


class FakeLocal:
    """LM Studio that starts (and loads a model) when asked: detect / lms / subprocess.run stand-ins."""

    def __init__(self, monkeypatch, models=("gemma",), jit=True):
        from afterclass import setup_ai

        self.running, self.loaded, self.calls = False, [], []
        self.models, self.jit = list(models), jit
        monkeypatch.setattr(setup_ai, "lms_bin", lambda: "/bin/lms")
        monkeypatch.setattr(setup_ai.shutil, "which", lambda name: None)
        monkeypatch.setattr(setup_ai, "detect", self.detect)
        monkeypatch.setattr(setup_ai, "installed_models", lambda app: [(m, 5) for m in self.models])
        monkeypatch.setattr(setup_ai.subprocess, "run", self.run)
        monkeypatch.setattr(setup_ai.time, "sleep", lambda s: None)

    def detect(self, url="auto"):
        shown = self.models if self.jit else self.loaded
        return [{"name": "LM Studio", "url": "http://localhost:1234", "kind": "openai", "models": shown}] if self.running else []

    def run(self, cmd, **kw):
        self.calls.append(cmd[1:])
        if cmd[1:3] == ["server", "start"]:
            self.running = True
        if cmd[1:3] == ["server", "stop"]:
            self.running = False
        if cmd[1] == "load":
            self.loaded.append(cmd[2])


def test_start_and_stop_the_local_server(monkeypatch):
    from afterclass import setup_ai

    lms = FakeLocal(monkeypatch)
    assert setup_ai.apps_brief()[0] == {"app": "lmstudio", "name": "LM Studio", "installed": True, "running": False}
    r = setup_ai.start_server()
    assert r["ok"] and r["app"] == "lmstudio" and lms.calls == [["server", "start"]]
    assert setup_ai.start_server()["ok"] and len(lms.calls) == 1  # already running: nothing started again
    assert setup_ai.stop_server("lmstudio")["ok"] and not lms.running
    with pytest.raises(ValueError):
        setup_ai.stop_server("nope")


def test_start_loads_a_model_when_lm_studio_has_none_ready(monkeypatch):
    from afterclass import setup_ai

    lms = FakeLocal(monkeypatch, jit=False)
    assert setup_ai.start_server()["ok"] and lms.loaded == ["gemma"]
    lms = FakeLocal(monkeypatch, models=())
    r = setup_ai.start_server()
    assert not r["ok"] and "no model" in r["message"]


def test_nothing_installed_and_autostart_off(monkeypatch):
    from afterclass import setup_ai

    monkeypatch.setattr(setup_ai, "lms_bin", lambda: None)
    monkeypatch.setattr(setup_ai.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup_ai, "detect", lambda url="auto": [])
    assert not setup_ai.start_server()["ok"]
    monkeypatch.setattr(setup_ai.threading, "Thread", lambda **kw: pytest.fail("autostart is off"))
    setup_ai.autostart()


def test_the_video_fallback_makes_the_window_fullscreen():
    class Window:
        toggled = 0

        def toggle_fullscreen(self):
            self.toggled += 1

    window, web = Window(), FakeWebview()
    web.active_window = lambda: window
    assert gui.PageApi(gui.Windows(gui.Api(), webview=web)).fullscreen() and window.toggled == 1
