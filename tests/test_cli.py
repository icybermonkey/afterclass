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
