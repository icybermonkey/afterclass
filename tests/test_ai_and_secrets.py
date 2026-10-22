import json
import os
import stat

import httpx2
import pytest

from lecap import config
from lecap.ai import claude as claude_mod
from lecap.ai.claude_code import ClaudeCodeProvider
from lecap.ai.schema import AiResult, json_schema
from lecap.model import Card, Chapter, Lesson, Segment
from lecap.steps import ai as ai_step

RESULT = {
    "lesson_title": "Code Obfuscation",
    "chapters": [{"start_card": 1, "title": "Opaque Predicates"}],
    "glossary": [{"term": "Opaque predicate", "definition": "A condition known a priori.", "card": 1}],
}
# What v0.2 cached in data/ai.json: must still be readable
OLD_RESULT = {**RESULT, "lesson_summary": "About obfuscation.",
              "chapters": [{"start_card": 1, "title": "Opaque Predicates", "summary": "S.", "tags": ["x"]}],
              "exam_questions": [{"question": "Why?", "answer": "Because.", "card": 1}], "key_points": []}


def lesson():
    cards = [Card(id=0, kind="slide", start=0, end=10, title="Intro", text="Welcome",
                  segments=[Segment(0, 5, "Hello everyone")]),
             Card(id=1, kind="slide", start=10, end=20, title="Opaque Predicates", deck_page=46,
                  segments=[Segment(11, 15, "An opaque predicate is...")])]
    return Lesson(id="x", title="Lesson 1", source="video", cards=cards,
                  chapters=[Chapter(title="Beginning", card=0)])


def test_schema_is_self_contained_and_strict():
    s = json_schema()
    text = json.dumps(s)
    assert "$ref" not in text and "$defs" not in text
    assert s["additionalProperties"] is False
    assert s["properties"]["chapters"]["items"]["additionalProperties"] is False

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    for obj in objects(s):  # every required field must be defined
        assert set(obj.get("required", [])) <= set(obj["properties"]), obj.get("required")
    assert "title" in s["properties"]["chapters"]["items"]["properties"]


def test_prompt_and_apply():
    les = lesson()
    system, prompt = ai_step.build_prompt(les)
    assert "[card 1 | 00:00:10 | slide | deck p.46] Opaque Predicates" in prompt
    assert "SPEECH: An opaque predicate is..." in prompt
    ai_step.apply(les, AiResult.model_validate(RESULT), {"provider": "test", "model": "m"})
    assert les.chapters[0].card == 0  # first chapter snaps to the first card
    assert les.ai["glossary"][0]["term"] == "Opaque predicate"
    assert ai_step.parse({**OLD_RESULT, "chapters": OLD_RESULT["chapters"] * 2}).chapters[0].title == "Opaque Predicates"


def test_claude_api_request_body(monkeypatch):
    """The API provider sends structured output + fallbacks and never needs a real key here."""
    pytest.importorskip("anthropic")
    import anthropic

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta", "")
        seen["auth"] = request.headers.get("x-api-key")
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
               "content": [{"type": "text", "text": json.dumps(RESULT)}], "stop_reason": "end_turn",
               "stop_sequence": None, "usage": {"input_tokens": 1000, "output_tokens": 500}}
        events = [
            ("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None,
                                                                    "usage": {"input_tokens": 1000, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": json.dumps(RESULT)}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 500}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        sse = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)
        return httpx2.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = anthropic.Anthropic(api_key="fake-test-key-123456789", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(claude_mod.ClaudeProvider, "_client", lambda self: client)
    data, usage = claude_mod.ClaudeProvider().run("sys", "prompt", json_schema(), "claude-opus-5-5")
    assert data["lesson_title"] == "Code Obfuscation"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["thinking"] == {"type": "adaptive"}
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert usage.cost_usd == pytest.approx((1000 * 4 + 500 * 20) / 1e6)


def test_claude_code_provider_with_fake_cli(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\n"
                    f"echo '{json.dumps({'is_error': False, 'structured_output': RESULT, 'usage': {'input_tokens': 5, 'output_tokens': 7}, 'modelUsage': {'claude-opus-5-5': {}}})}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    p = ClaudeCodeProvider()
    assert p.available()[0]
    data, usage = p.run("sys", "prompt", json_schema(), "opus")
    assert data == RESULT and usage.model == "claude-opus-5-5"


def test_secret_order_env_before_keychain(monkeypatch):
    class FakeKeyring:
        store = {("lecap", "claude"): "fake-from-keychain-abcd"}

        def get_password(self, s, u):
            return self.store.get((s, u))

    monkeypatch.setattr(config, "_keyring", lambda: FakeKeyring())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.get_secret("claude") == ("fake-from-keychain-abcd", "macOS Keychain")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-env-key-wxyz")
    assert config.get_secret("claude")[1] == "env $ANTHROPIC_API_KEY"
    assert config.mask("fake-env-key-wxyz") == "fake-en…wxyz"


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LECAP_CONFIG", str(tmp_path / "c.toml"))
    config.set_value("threshold", "6")
    config.set_value("language", 'de"x')
    cfg = config.load()
    assert cfg["threshold"] == 6.0 and cfg["language"] == 'de"x'
    assert "key" not in (tmp_path / "c.toml").read_text().lower().replace("api_key", "")


def test_cached_ai_result_survives_renumbered_cards():
    les = lesson()
    times = ai_step.card_times(les)  # card 1 starts at 10s
    # A break card is inserted before it: the same moment is now card 2
    les.cards.insert(1, Card(id=1, kind="break", start=5, end=10))
    les.cards[2].id = 2
    ai_step.apply(les, AiResult.model_validate(RESULT), {"provider": "test", "model": "m"}, times)
    assert les.ai["glossary"][0]["card"] == 2


def test_placeholder_answers_are_rejected(tmp_path, monkeypatch):
    from lecap.ui import LecapError

    placeholder = {**RESULT, "chapters": RESULT["chapters"][:1], "glossary": []}
    with pytest.raises(LecapError, match="nearly empty"):
        ai_step.parse(placeholder)
    # Claude Code reporting a safety stop is retried, then fails with a clear message
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\n"
                    f"echo '{json.dumps({'is_error': False, 'safety_stops': 1, 'structured_output': placeholder})}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(LecapError, match="safety classifier"):
        ClaudeCodeProvider().run("sys", "prompt", json_schema(), "opus")


def test_glossary_duplicates_and_short_chapters_are_merged():
    les = lesson()
    les.duration = 1000
    data = {**RESULT,
            "chapters": [{"start_card": 0, "title": "A"}, {"start_card": 1, "title": "B"}],
            "glossary": [{"term": "Opaque Predicates", "definition": "1", "card": 1},
                         {"term": "opaque predicate", "definition": "2", "card": 1},
                         {"term": "Control Flow Flattening (CFF)", "definition": "3", "card": 1}]}
    ai_step.apply(les, AiResult.model_validate(data), {"provider": "t", "model": "m"})
    assert [g["term"] for g in les.ai["glossary"]] == ["Opaque Predicates", "Control Flow Flattening (CFF)"]
    assert [c.title for c in les.chapters] == ["B"]  # "A" lasted 10 s and merged into the next chapter
