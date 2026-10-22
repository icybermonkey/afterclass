"""Write index.html: the template with the lesson JSON embedded (works from file://, no server)."""

import html
from importlib.resources import files


def render(lesson, out_path):
    template = files("lecap").joinpath("web/template.html").read_text(encoding="utf-8")
    data = lesson.to_json().replace("</", "<\\/")  # keep </script> inside strings harmless
    page = (template
            .replace("__TITLE__", html.escape((lesson.ai or {}).get("title") or lesson.title))
            .replace("/*LESSON_DATA*/", data))
    out_path.write_text(page, encoding="utf-8")
    return len(page.encode())
