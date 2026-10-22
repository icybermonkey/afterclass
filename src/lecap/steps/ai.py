"""--ai: chapters, summaries, tags, glossary and exam questions from any provider."""

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
        return AiResult.model_validate(data)
    except ValidationError as e:
        raise LecapError(f"the AI answer does not match the expected format: {e.errors()[0]['msg']}")


def apply(lesson, result, meta):
    """Use AI chapters (snapped to valid card ids) and store the extras on the lesson."""
    valid = {c.id for c in lesson.cards}
    chapters, seen = [], set()
    for ch in sorted(result.chapters, key=lambda c: c.start_card):
        card = ch.start_card if ch.start_card in valid else min(valid, key=lambda v: abs(v - ch.start_card))
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
        "glossary": [t.model_dump() for t in result.glossary],
        "questions": [q.model_dump() for q in result.exam_questions],
        **meta,
    }


SCHEMA = json_schema()
