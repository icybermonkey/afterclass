"""Write index.html: the template with the lesson JSON embedded (works from file://, no server)."""

import base64
import html
import json
from importlib.resources import files

from .. import APP_NAME


def ask_config(folder):
    """What the page needs to ask a local model directly when opened as a plain file:
    the same prompt and answer schema as `afterclass open` uses, and the command to open it properly."""
    from ..ai.schema import AskAnswer, json_schema
    from ..ask import ASK_SYSTEM, STOP

    return {"system": ASK_SYSTEM, "schema": json_schema(AskAnswer), "stop": sorted(STOP),
            "open_cmd": f"{APP_NAME} open '{folder}'"}


def page_html(lesson_json, title, folder):
    """The page: the current template with the lesson JSON, the folder's notes and the Ask config embedded."""
    template = files("afterclass").joinpath("web/template.html").read_text(encoding="utf-8")
    # The folder's notes (from `afterclass open`) are embedded too, so a plain file:// view shows them
    notes_file = folder / "notes" / "notes.json"
    notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "{}"
    return (template
            .replace("__TITLE__", html.escape(title))
            .replace("/*LESSON_DATA*/", _safe(lesson_json))
            .replace("/*NOTES_DATA*/", _safe(notes))
            .replace("/*ASK_DATA*/", _safe(json.dumps(ask_config(folder), ensure_ascii=False)))
            .replace("__ICON__", icon_uri()))


def render(lesson, out_path):
    page = page_html(lesson.to_json(), (lesson.ai or {}).get("title") or lesson.title, out_path.parent)
    out_path.write_text(page, encoding="utf-8")
    return len(page.encode())


def refresh(folder):
    """Rewrite folder/index.html from data/lesson.json with the current template, so lessons built by an
    older afterclass get its page changes. Writes only when something changed. Returns True if it did."""
    data = (folder / "data" / "lesson.json").read_text(encoding="utf-8")
    lesson = json.loads(data)
    page = page_html(data, (lesson.get("ai") or {}).get("title") or lesson.get("title") or folder.name, folder)
    out = folder / "index.html"
    if out.exists() and out.read_text(encoding="utf-8") == page:
        return False
    out.write_text(page, encoding="utf-8")
    return True


def icon_uri():
    return "data:image/svg+xml;base64," + base64.b64encode(files(APP_NAME).joinpath("assets/afterclass.svg").read_bytes()).decode()


def _safe(json_text):
    return json_text.replace("</", "<\\/")  # keep </script> inside strings harmless
