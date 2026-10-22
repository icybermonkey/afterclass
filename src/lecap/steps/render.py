"""Write index.html: the template with the lesson JSON embedded (works from file://, no server)."""

import html
from importlib.resources import files


def render(lesson, out_path):
    template = files("lecap").joinpath("web/template.html").read_text(encoding="utf-8")
    # The folder's notes (from `lecap open`) are embedded too, so a plain file:// view shows them
    notes_file = out_path.parent / "notes" / "notes.json"
    notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "{}"
    page = (template
            .replace("__TITLE__", html.escape((lesson.ai or {}).get("title") or lesson.title))
            .replace("/*LESSON_DATA*/", _safe(lesson.to_json()))
            .replace("/*NOTES_DATA*/", _safe(notes)))
    out_path.write_text(page, encoding="utf-8")
    return len(page.encode())


def _safe(json_text):
    return json_text.replace("</", "<\\/")  # keep </script> inside strings harmless
