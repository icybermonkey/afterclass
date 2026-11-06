import pytest

from afterclass import cli
from afterclass.sources import registry
from afterclass.ui import AfterclassError


@pytest.mark.parametrize("name,kind", [("a.mp3", "AUDIO"), ("b.pdf", "PDF"), ("c.pptx", "PPTX")])
def test_planned_inputs_have_clear_message(tmp_path, name, kind):
    with pytest.raises(AfterclassError, match=f"{kind} input is not supported yet"):
        registry.pick(tmp_path / name)


def test_unknown_input():
    with pytest.raises(AfterclassError, match="don't know how to read"):
        registry.pick(__import__("pathlib").Path("x.xyz"))


def test_refuses_foreign_output_folder(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "thesis.docx").write_text("important")
    assert cli.main([str(video), str(out)]) == 1
    assert (out / "thesis.docx").exists()


def _lesson(tmp_path, deck=True, speakers=True):
    import json

    folder = tmp_path / "Lesson 2.afterclass"
    (folder / "data").mkdir(parents=True)
    lesson = {"title": "SPCS - Lesson 2", "language": "de", "media": "media/rec.mp4",
              "deck": "media/slides.pdf" if deck else None}
    (folder / "data" / "lesson.json").write_text(json.dumps(lesson))
    if speakers:
        (folder / "data" / "speakers.json").write_text("[]")
    return folder


def test_enrich_refuses_non_lesson_folder(tmp_path):
    (tmp_path / "thesis.docx").write_text("important")
    assert cli.main(["enrich", str(tmp_path)]) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["thesis.docx"]


def test_enrich_rebuilds_with_lesson_settings(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run", lambda args: seen.append(args) or 0)
    folder = _lesson(tmp_path)
    assert cli.main(["enrich", str(folder)]) == 0
    a = seen[0]
    assert a.input == folder / "media" / "rec.mp4" and a.out == folder
    assert a.deck == folder / "media" / "slides.pdf"
    assert (a.title, a.language, a.ai, a.speakers, a.redo) == ("SPCS - Lesson 2", "de", "default", None, None)


def test_enrich_options(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run", lambda args: seen.append(args) or 0)
    folder = _lesson(tmp_path, deck=False, speakers=False)
    assert cli.main(["enrich", str(folder), "--ai", "claude-code", "--ai-model", "sonnet", "--redo", "-y"]) == 0
    a = seen[0]
    assert a.deck is None and a.speakers is False
    assert (a.ai, a.ai_model, a.redo, a.yes) == ("claude-code", "sonnet", ["ai"], True)
