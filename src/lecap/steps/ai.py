"""--ai: chapters, summaries, tags, glossary and exam questions from any provider."""

import bisect

from pydantic import ValidationError

from ..ai.schema import AiResult, json_schema
from ..model import Chapter
from ..ui import LecapError, fmt_time

LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "it": "Italian", "es": "Spanish"}

SYSTEM = """You turn a recorded university lecture into study material.
You get the lecture as numbered cards in time order. Each card is one slide or screen and carries the
slide text and what the lecturer said while it was shown. Base everything on this material only.
Write in {language}. Keep technical terms as the lecturer uses them."""

TASK = """Lesson: {title}
Detected chapter hints ({hint_source}):
{hints}

Create:
- chapters: 4-12 chapters following the structure of the lecture, each starting at a card id, in order.
  The first chapter starts at card {first}. Use the hints where they fit; skip organisational chatter only
  if it is short. Each chapter gets a 2-4 sentence summary and 3-6 lowercase tags.
- glossary: 8-20 important terms with a short definition as explained in the lecture.
- exam_questions: 6-10 questions a student should be able to answer, with short answers.
- lesson_title and lesson_summary.

Cards:
{cards}"""


def build_prompt(lesson):
    lines = []
    for c in lesson.cards:
        if c.kind == "break":
            continue
        head = f"[card {c.id} | {fmt_time(c.start)} | {c.kind}"
        if c.deck_page:
            head += f" | deck p.{c.deck_page}"
        head += f"] {c.title}".rstrip()
        lines.append(head)
        if c.text and c.kind == "slide":
            lines.append("SLIDE: " + " ".join(c.text.split())[:600])
        speech = " ".join(s.text for s in c.segments)
        if speech:
            lines.append("SPEECH: " + speech)
    hints = "\n".join(f"- card {ch.card}: {ch.title}" for ch in lesson.chapters) or "- none"
    first = next((c.id for c in lesson.cards if c.kind != "break"), 0)
    system = SYSTEM.format(language=LANG_NAMES.get(lesson.language, lesson.language))
    prompt = TASK.format(title=lesson.title, hint_source=lesson.stats.get("chapter_source", "slides"),
                         hints=hints, first=first, cards="\n".join(lines))
    return system, prompt


def parse(data):
    try:
        result = AiResult.model_validate(data)
    except ValidationError as e:
        raise LecapError(f"the AI answer does not match the expected format: {e.errors()[0]['msg']}")
    # Valid JSON can still be a placeholder ("could not generate ..."): never cache or show that
    if len(result.chapters) < 2 or not result.glossary or not result.exam_questions:
        raise LecapError(f"the AI answer is nearly empty ({len(result.chapters)} chapters, "
                         f"{len(result.glossary)} terms, {len(result.exam_questions)} questions): "
                         f"{result.lesson_summary[:160]}")
    return result


def card_times(lesson):
    """{card id: start} at the time of the AI call, so cached results survive re-numbered cards."""
    return {str(c.id): c.start for c in lesson.cards}


def _remap(lesson, times):
    """Old card id -> current card id (via start times when the cards changed)."""
    starts = [c.start or 0 for c in lesson.cards]

    def to_current(old):
        t = (times or {}).get(str(old))
        if t is None:
            return min(max(old, 0), len(lesson.cards) - 1)
        return max(bisect.bisect_right(starts, t + 0.5) - 1, 0)

    return to_current


def apply(lesson, result, meta, times=None):
    """Use AI chapters and store the extras on the lesson. times: card_times() from the AI call."""
    to_current = _remap(lesson, times)
    chapters, seen = [], set()
    for ch in sorted(result.chapters, key=lambda c: c.start_card):
        card = lesson.cards[to_current(ch.start_card)].id
        if card in seen:
            continue
        seen.add(card)
        chapters.append(Chapter(title=ch.title, card=card, source="ai", summary=ch.summary,
                                tags=[t.lower() for t in ch.tags]))
    if chapters:
        if chapters[0].card != lesson.cards[0].id:
            chapters[0].card = lesson.cards[0].id
        lesson.stats["slide_chapters"] = [{"title": c.title, "card": c.card} for c in lesson.chapters]
        lesson.chapters = chapters
    lesson.ai = {
        "title": result.lesson_title,
        "summary": result.lesson_summary,
        "glossary": [{**t.model_dump(), "card": to_current(t.card)} for t in result.glossary],
        "questions": [{**q.model_dump(), "card": to_current(q.card)} for q in result.exam_questions],
        **meta,
    }


SCHEMA = json_schema()
