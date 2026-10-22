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
