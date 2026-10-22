"""Write index.html: the template with the lesson JSON embedded (works from file://, no server)."""

import base64
import html
import json
from importlib.resources import files

from .. import APP_NAME


def ask_config(folder):
    """What the page needs to ask a local model directly when opened as a plain file:
    the same prompt and answer schema as `lecap open` uses, and the command to open it properly."""
    from ..ai.schema import AskAnswer, json_schema
    from ..ask import ASK_SYSTEM, STOP

    return {"system": ASK_SYSTEM, "schema": json_schema(AskAnswer), "stop": sorted(STOP),
            "open_cmd": f"{APP_NAME} open '{folder}'"}


def render(lesson, out_path):
    template = files("lecap").joinpath("web/template.html").read_text(encoding="utf-8")
    # The folder's notes (from `lecap open`) are embedded too, so a plain file:// view shows them
    notes_file = out_path.parent / "notes" / "notes.json"
    notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "{}"
    page = (template
            .replace("__TITLE__", html.escape((lesson.ai or {}).get("title") or lesson.title))
            .replace("/*LESSON_DATA*/", _safe(lesson.to_json()))
            .replace("/*NOTES_DATA*/", _safe(notes))
            .replace("/*ASK_DATA*/", _safe(json.dumps(ask_config(out_path.parent), ensure_ascii=False)))
            .replace("__ICON__", icon_uri()))
    out_path.write_text(page, encoding="utf-8")
    return len(page.encode())


def icon_uri():
    return "data:image/svg+xml;base64," + base64.b64encode(files(APP_NAME).joinpath("assets/lecap.svg").read_bytes()).decode()


def _safe(json_text):
    return json_text.replace("</", "<\\/")  # keep </script> inside strings harmless
