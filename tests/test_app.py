import plistlib

from lecap import gui, macapp


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
    app = macapp.build(tmp_path / "lecap.app", command=["/usr/local/bin/lecap"])
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "lecap" and info["CFBundleIconFile"] == "lecap"
    exe = app / "Contents/MacOS/lecap"
    text = exe.read_text()
    assert exe.stat().st_mode & 0o111 and '/opt/homebrew/bin' in text and text.rstrip().endswith('exec "/usr/local/bin/lecap" app')
    assert (app / "Contents/Resources/lecap.icns").stat().st_size > 1000


def test_media_choice_is_passed_on():
    assert gui.build_args(form(media="move"))[0][-1] == "--move"
    assert gui.build_args(form(media="link"))[0][-1] == "--link"


def test_rerun_finds_files_that_were_moved_into_the_lesson(tmp_path):
    from pathlib import Path

    from lecap import cli

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

    from lecap import ui

    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    ui.notify("lecap: lesson ready", 'SPCS "Lesson 1" is ready')
    assert calls[0][:2] == ["osascript", "-e"]
    assert calls[0][2] == 'display notification "SPCS \\"Lesson 1\\" is ready" with title "lecap: lesson ready" sound name "Glass"'
