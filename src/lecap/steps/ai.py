"""--ai: chapters, summaries, tags, glossary, exam questions and key points from any provider."""

import bisect
import math
import re

from pydantic import ValidationError

from ..ai.schema import AiLessonSummary, AiResult, json_schema
from ..model import Chapter
from ..ui import LecapError, fmt_time, info

LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "it": "Italian", "es": "Spanish"}
LONG_WORDS = 250  # cards with more speech than this get key points

SYSTEM = """You turn a recorded university lecture into study material.
You get the lecture as numbered cards in time order. Each card is one slide or screen and carries the
slide text and what was said while it was shown. Base everything on this material only.
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
- key_points: for every card marked LONG, 2-4 short key points of what is said there.
- lesson_title and lesson_summary.

Cards:
{cards}"""

TASK_PART = """Lesson: {title}. This is part {part} of {parts} of the lecture (cards {a}-{b}).
Chapter hints in this part: {hints}

Create, for this part only:
- chapters: 1-4 chapters, each starting at a card id of this part, in order; 2-3 sentence summary, 3-5 lowercase tags.
- glossary: 3-8 important terms explained in this part.
- exam_questions: 2-4 questions with short answers.
- key_points: for every card marked LONG, 2-4 short key points.
- lesson_title and lesson_summary: describe this part.

Cards:
{cards}"""

SUMMARY_TASK = """These are the chapters of a lecture titled "{title}":
{chapters}

Give the lesson a short title and a 4-6 sentence summary."""


def card_lines(lesson, cards=None):
    lines = []
    for c in cards or lesson.cards:
        if c.kind == "break":
            continue
        speech = " ".join(s.text for s in c.segments)
        head = f"[card {c.id} | {fmt_time(c.start)} | {c.kind}"
        if c.deck_page:
            head += f" | deck p.{c.deck_page}"
        if len(speech.split()) > LONG_WORDS:
            head += " | LONG"
        lines.append((head + f"] {c.title}").rstrip())
        if c.text and c.kind == "slide":
            lines.append("SLIDE: " + " ".join(c.text.split())[:600])
        if speech:
            lines.append("SPEECH: " + speech)
    return lines


def _system(lesson):
    return SYSTEM.format(language=LANG_NAMES.get(lesson.language, lesson.language))


def build_prompt(lesson):
    hints = "\n".join(f"- card {ch.card}: {ch.title}" for ch in lesson.chapters) or "- none"
    first = next((c.id for c in lesson.cards if c.kind != "break"), 0)
    prompt = TASK.format(title=lesson.title, hint_source=lesson.stats.get("chapter_source", "slides"),
                         hints=hints, first=first, cards="\n".join(card_lines(lesson)))
    return _system(lesson), prompt


def tokens(text):
    return len(text) // 3  # rough and on the safe side for mixed prose/code


def run(provider, lesson, model):
    """Run the AI step; splits the lecture into parts when the model's context is too small."""
    system, prompt = build_prompt(lesson)
    limit = provider.context_limit(model) if hasattr(provider, "context_limit") else None
    if not limit or tokens(system + prompt) + 8000 <= limit:
        data, usage = provider.run(system, prompt, SCHEMA, model)
        return parse(data), usage
    return _run_parts(provider, lesson, model, limit)


def _run_parts(provider, lesson, model, limit):
    budget = max(limit - 6000, 2000) * 3  # characters of cards per part
    sizes = [sum(len(x) for x in card_lines(lesson, [c])) for c in lesson.cards]
    # Equal-sized parts (not "fill one, leave a scrap"): a tiny last part makes tiny chapters
    target = sum(sizes) / max(math.ceil(sum(sizes) / budget), 1)
    parts, current, size = [], [], 0
    for c, n in zip(lesson.cards, sizes):
        if current and (size + n > budget or size >= target):
            parts.append(current)
            current, size = [], 0
        current.append(c)
        size += n
    if current:
        parts.append(current)
    info(f"the model's context ({limit:,} tokens) is too small for the whole lecture: "
         f"working in {len(parts)} parts")
    system = _system(lesson)
    chapters, glossary, questions, points, total_in, total_out = [], {}, [], [], 0, 0
    for k, cards in enumerate(parts, 1):
        ids = {c.id for c in cards}
        hints = ", ".join(f"card {ch.card}: {ch.title}" for ch in lesson.chapters if ch.card in ids) or "none"
        prompt = TASK_PART.format(title=lesson.title, part=k, parts=len(parts), a=cards[0].id, b=cards[-1].id,
                                  hints=hints, cards="\n".join(card_lines(lesson, cards)))
        info(f"part {k}/{len(parts)} (cards {cards[0].id}-{cards[-1].id})…")
        data, usage = provider.run(system, prompt, SCHEMA, model)
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        try:
            part = AiResult.model_validate(data)
        except ValidationError:
            continue
        chapters += [ch for ch in part.chapters if ch.start_card in ids] or part.chapters[:1]
        for t in part.glossary:
            glossary.setdefault(term_key(t.term), t)
        questions += part.exam_questions
        points += part.key_points
    overview = "\n".join(f"- {ch.title}: {ch.summary}" for ch in chapters)
    data, usage = provider.run(system, SUMMARY_TASK.format(title=lesson.title, chapters=overview),
                               json_schema(AiLessonSummary), model)
    total_in += usage.input_tokens
    total_out += usage.output_tokens
    head = AiLessonSummary.model_validate(data)
    step = max(len(questions) // 10, 1)
    merged = AiResult(lesson_title=head.lesson_title, lesson_summary=head.lesson_summary, chapters=chapters,
                      glossary=list(glossary.values())[:20], exam_questions=questions[::step][:10], key_points=points)
    usage.input_tokens, usage.output_tokens = total_in, total_out
    return parse(merged.model_dump()), usage


def term_key(term):
    """'Opaque Predicates', 'opaque predicate' and 'Control Flow Flattening (CFF)' -> one key each."""
    t = re.sub(r"\(.*?\)", "", term).strip().lower()
    return t[:-1] if t.endswith("s") and len(t) > 4 else t


def parse(data):
    if isinstance(data, dict):
        data = {"key_points": [], **data}  # results cached before key points existed
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
    # Chapters shorter than 3 minutes merge into the next one (small models like to over-split)
    starts = [lesson.cards[ch.card].start or 0 for ch in chapters] + [lesson.duration or float("inf")]
    chapters = [ch for k, ch in enumerate(chapters) if k == len(chapters) - 1 or starts[k + 1] - starts[k] >= 180]
    if chapters:
        if chapters[0].card != lesson.cards[0].id:
            chapters[0].card = lesson.cards[0].id
        lesson.stats["slide_chapters"] = [{"title": c.title, "card": c.card} for c in lesson.chapters]
        lesson.chapters = chapters
    for kp in result.key_points:
        lesson.cards[to_current(kp.card)].points = kp.points[:4]
    lesson.ai = {
        "title": result.lesson_title,
        "summary": result.lesson_summary,
        "glossary": [{**t.model_dump(), "card": to_current(t.card)}
                     for t in {term_key(t.term): t for t in reversed(result.glossary)}.values()][::-1],
        "questions": [{**q.model_dump(), "card": to_current(q.card)} for q in result.exam_questions],
        **meta,
    }


SCHEMA = json_schema()
