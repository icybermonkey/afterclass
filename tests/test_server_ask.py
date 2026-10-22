import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lecap import ask, config, server
from lecap.ai.local import LocalProvider
from lecap.ai.schema import json_schema
from lecap.model import Card, Chapter, Lesson, Segment
from lecap.steps import ai as ai_step


def lesson_dict():
    def seg(t, text, sp="A", para=False):
        return {"start": t, "end": t + 3, "text": text, "speaker": sp, "para": para}

    cards = [
        {"id": 0, "kind": "slide", "start": 0, "end": 60, "title": "Assessment", "text": "no exam, four graded task sheets",
         "segments": [seg(1, "Welcome to the course.", para=True), seg(5, "There is no exam.")]},
        {"id": 1, "kind": "slide", "start": 60, "end": 120, "title": "Opaque Predicates", "text": "known truth value",
         "segments": [seg(61, "An opaque predicate has a known outcome.", para=True)]},
        {"id": 2, "kind": "slide", "start": 120, "end": 180, "title": "Virtual Machines", "text": "bytecode handlers dispatcher",
         "segments": [seg(121, "Now we talk about virtual machines.", para=True), seg(125, "Is it fast?", "B", True)]},
        {"id": 3, "kind": "demo", "start": 180, "end": 240, "title": "", "text": "",
         "segments": [seg(181, "Back to virtual machines in the demo.", para=True)]},
    ]
    return {"id": "x", "title": "Test lesson", "duration": 240, "cards": cards,
            "chapters": [{"title": "Intro", "card": 0}, {"title": "VMs", "card": 2}], "ai": None}


def test_bm25_and_first_mention():
    idx = ask.Index(ask.passages(lesson_dict()))
    assert ask.first_mention(idx, "where do we start talking about virtual machines?")["t"] == 120
    assert ask.first_mention(idx, "is there an exam")["card"] == 0
    assert ask.words("graded grading grade") == ["grad", "grad", "grad"]


def test_offline_answer_without_model():
    r = ask.answer(lesson_dict(), "virtual machines", provider=None)
    assert r["provider"] is None and r["answer"] == "" and r["main_time"] == 120 and r["sources"]


# --- server ---------------------------------------------------------------

@pytest.fixture
def lesson_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LECAP_CONFIG", str(tmp_path / "config.toml"))
    folder = tmp_path / "lesson"
    (folder / "data").mkdir(parents=True)
    (folder / "media").mkdir()
    (folder / "index.html").write_text("<html>ok</html>")
    (folder / "data" / "lesson.json").write_text(json.dumps(lesson_dict()))
    (folder / "media" / "v.mp4").write_bytes(bytes(range(256)) * 4)
    httpd = server.make_server(folder, 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield folder, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    h = {"X-Lecap": "1", "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_server_guards(lesson_folder):
    _folder, base = lesson_folder
    assert call(base + "/api/state", headers={"X-Lecap": ""})[0] == 403
    assert call(base + "/api/state", headers={"Origin": "https://evil.example"})[0] == 403
    assert call(base + "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert call(base + "/notes/notes.json")[0] == 404
    assert call(base + "/api/state")[0] == 200


def test_notes_are_saved_atomically_in_the_folder(lesson_folder):
    folder, base = lesson_folder
    status, _, _ = call(base + "/api/notes", "PUT", {"stars": {"2": True}, "notes": {"2": "VMs!"}, "evil": 1})
    assert status == 200
    saved = json.loads((folder / "notes" / "notes.json").read_text())
    assert saved == {"stars": {"2": True}, "notes": {"2": "VMs!"}, "speakers": {}}
    assert not list((folder / "notes").glob(".*.tmp"))


def test_range_requests_for_seeking(lesson_folder):
    _folder, base = lesson_folder
    status, body, headers = call(base + "/media/v.mp4", headers={"Range": "bytes=10-19"})
    assert status == 206 and body == bytes(range(10, 20)) and headers["Content-Range"] == "bytes 10-19/1024"


def test_settings_never_return_keys(lesson_folder, monkeypatch):
    _folder, base = lesson_folder
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-1234567890abcd")
    status, body, _ = call(base + "/api/settings")
    assert status == 200 and b"fake-key-1234567890abcd" not in body and b"abcd" in body  # masked only
    status, body, _ = call(base + "/api/settings", "PUT", {"media": "delete"})
    assert status == 400


def test_ask_search_mode_and_online_gate(lesson_folder, monkeypatch):
    _folder, base = lesson_folder
    status, body, _ = call(base + "/api/ask", "POST", {"question": "virtual machines", "mode": "search"})
    assert status == 200
    assert json.loads(body)["main_time"] == 120  # the "Virtual Machines" slide, without any model
    config.set_value("allow_online", "off")
    status, body, _ = call(base + "/api/ask", "POST", {"question": "what is x", "mode": "general"})
    assert status == 403


# --- local provider against a fake OpenAI-compatible server --------------------

class FakeLMStudio(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._send({"data": [{"id": "text-embedding-x"}, {"id": "tiny-model"}]})
        if self.path == "/api/v0/models":
            return self._send({"data": [{"id": "tiny-model", "loaded_context_length": 9000}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLMStudio.calls.append(req)
        schema = req["response_format"]["json_schema"]["schema"]
        if list(schema["properties"]) == ["lesson_summary"]:
            content = {"lesson_summary": "All parts together."}
        elif "chapters" in schema["properties"]:
            first = int(req["messages"][1]["content"].split("(cards ")[1].split("-")[0]) if "(cards " in req["messages"][1]["content"] else 0
            content = {"lesson_title": "Whole lesson", "lesson_summary": f"Part {first}.",
                       "chapters": [{"start_card": first, "title": f"Part {first}"},
                                    {"start_card": first + 1, "title": f"Part {first}b"}],
                       "glossary": [{"term": f"T{first}", "definition": "d", "card": first}]}
        else:
            content = {"answer": "It starts at 2:00, where the lecturer introduces virtual machines.",
                       "main_time": 121.0, "sources": [181.0], "in_lesson": True}
        self._send({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


def test_local_provider_and_parts(monkeypatch, tmp_path):
    monkeypatch.setenv("LECAP_CONFIG", str(tmp_path / "c.toml"))
    httpd = HTTPServer(("127.0.0.1", 0), FakeLMStudio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    config.set_value("local_llm_url", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    p = LocalProvider()
    assert p.available() == (True, "")
    assert p.pick_model("") == "tiny-model"  # embedding models are skipped
    assert p.context_limit("tiny-model") == 9000

    # A lesson too long for 9000 tokens is done in parts, then merged
    cards = [Card(id=i, kind="slide", start=i * 60.0, end=i * 60.0 + 60, title=f"S{i}",
                  segments=[Segment(i * 60.0, i * 60.0 + 5, "word " * 900)]) for i in range(8)]
    les = Lesson(id="y", title="Long", source="video", cards=cards, chapters=[Chapter("C", 0)])
    FakeLMStudio.calls.clear()
    result, usage = ai_step.run(p, les, "tiny-model")
    assert len(FakeLMStudio.calls) >= 3  # several parts + one call that merges their summaries
    assert result.lesson_summary == "All parts together."
    assert result.lesson_title == "Whole lesson"
    assert len(result.chapters) >= 4 and usage.input_tokens == 100 * len(FakeLMStudio.calls)
    with pytest.raises(Exception, match="web"):
        p.run("s", "p", json_schema(), "tiny-model", web=True)
    httpd.shutdown()


def test_model_answer_has_own_words_main_time_and_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("LECAP_CONFIG", str(tmp_path / "c.toml"))
    httpd = HTTPServer(("127.0.0.1", 0), FakeLMStudio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    config.set_value("local_llm_url", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    r = ask.answer(lesson_dict(), "where do we start talking about virtual machines?", LocalProvider(), "", t=30)
    assert r["answer"].startswith("It starts at 2:00")
    assert r["main_time"] == 121.0
    assert [s["t"] for s in r["sources"]] == [181.0] and "virtual machines" in r["sources"][0]["text"]
    prompt = FakeLMStudio.calls[-1]["messages"][1]["content"]
    assert "Use that only if the question says" in prompt  # the current moment is context, not the topic
    httpd.shutdown()


def test_start_questions_use_the_earliest_strong_match(monkeypatch, tmp_path):
    """A model that points into the middle of a topic is corrected to where the topic starts."""
    class Late:
        name = "fake"

        def run(self, system, prompt, schema, model, **kw):
            from lecap.ai.base import Usage
            return {"answer": "VMs come up in the demo.", "main_time": 181.0, "sources": [], "in_lesson": True}, Usage(model="m")

    r = ask.answer(lesson_dict(), "where do we start talking about virtual machines?", Late(), "")
    assert r["main_time"] == 120 and [s["t"] for s in r["sources"]] == [181.0]
    r = ask.answer(lesson_dict(), "what happens in the virtual machine demo?", Late(), "")
    assert r["main_time"] == 181.0  # not a "start" question: the model's choice stays


def test_page_embeds_the_same_ask_prompt(tmp_path):
    """The file:// page asks local models directly; its prompt, schema and stop words come from ask.py."""
    from lecap.ai.schema import AskAnswer
    from lecap.steps import render

    cfg = render.ask_config(tmp_path)
    assert cfg["system"] == ask.ASK_SYSTEM
    assert cfg["schema"] == json_schema(AskAnswer)
    assert set(cfg["stop"]) == ask.STOP
    assert cfg["open_cmd"] == f"lecap open '{tmp_path}'"
    les = Lesson(id="z", title="T", source="video", cards=[Card(id=0, kind="slide", start=0, end=1)])
    out = tmp_path / "index.html"
    render.render(les, out)
    page = out.read_text()
    assert "/*ASK_DATA*/" not in page and '"main_time"' in page


def test_model_picker_options_and_choice(monkeypatch, tmp_path):
    from lecap import setup_ai
    from lecap.ai.registry import PROVIDERS

    monkeypatch.setenv("LECAP_CONFIG", str(tmp_path / "c.toml"))
    monkeypatch.setattr(setup_ai, "status", lambda: {"apps": [
        {"name": "LM Studio", "running": True, "loaded": ["gemma"], "models": [{"name": "gemma"}, {"name": "qwen"}]},
        {"name": "Ollama", "running": False, "loaded": [], "models": []}]})
    for name in ("claude", "claude-code"):
        monkeypatch.setattr(PROVIDERS[name], "available", lambda: (False, "no"))
    config.set_value("ask_provider", "local")
    config.set_value("ask_model", "qwen")
    m = server.model_options()
    local = [(o["model"], o["online"]) for o in m["options"] if o["provider"] == "local"]
    assert local == [("gemma", False), ("qwen", False)]
    assert all(o["online"] and not o["available"] for o in m["options"] if o["provider"].startswith("claude"))
    assert m["current"] == {"provider": "local", "model": "qwen"}
