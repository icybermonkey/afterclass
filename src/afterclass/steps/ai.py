"""--ai: chapter names, quick titles per card and a glossary from any provider (local model or Claude)."""

import bisect
import math
import re

from pydantic import ValidationError

from ..ai.schema import AiResult, AiSummary, json_schema
from ..model import Chapter
from ..ui import AfterclassError, fmt_time, info

LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "it": "Italian", "es": "Spanish"}

SYSTEM = """You structure a recorded university lecture for students.
You get the lecture as numbered cards in time order. Each card is one slide or screen and carries the
slide text and what was said while it was shown. Base everything on this material only.
Write in {language}. Keep technical terms as the lecturer uses them."""

TASK = """Lesson: {title}
Detected chapter hints ({hint_source}):
{hints}

Create:
- chapters: 4-12 chapters following the structure of the lecture, each starting at a card id, in order.
  The first chapter starts at card {first}. Use the hints where they fit. Titles: 2-6 words.
- glossary: 8-20 important terms with a short definition as explained in the lecture.
- card_titles: for EVERY card, a quick title of 3-8 words saying what is said or shown there
  (start live demos with "Demo:"). Concrete, not just the slide title.
- lesson_title: a short title for the lesson.
- lesson_summary: 4-6 sentences on what the lesson covers and its main points.

Cards:
{cards}"""

TASK_PART = """Lesson: {title}. This is part {part} of {parts} of the lecture (cards {a}-{b}).
Chapter hints in this part: {hints}

Create, for this part only:
- chapters: 1-4 chapters, each starting at a card id of this part, in order. Titles: 2-6 words.
- glossary: 3-8 important terms explained in this part.
- card_titles: for EVERY card of this part, a quick title of 3-8 words saying what is said or shown there
  (start live demos with "Demo:").
- lesson_title: a short title for the whole lesson.
- lesson_summary: 2-3 sentences on what this part covers.

Cards:
{cards}"""


SUMMARY_TASK = """These are summaries of the parts of a lecture titled "{title}", in order:
{parts}

Write lesson_summary: 4-6 sentences on what the whole lesson covers and its main points."""


def card_lines(lesson, cards=None):
    lines = []
    for c in cards or lesson.cards:
        if c.kind == "break":
            continue
        head = f"[card {c.id} | {fmt_time(c.start)} | {c.kind}"
        if c.deck_page:
            head += f" | deck p.{c.deck_page}"
        lines.append((head + f"] {c.title}").rstrip())
        if c.text and c.kind == "slide":
            lines.append("SLIDE: " + " ".join(c.text.split())[:600])
        speech = " ".join(s.text for s in c.segments)
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
    if not limit or tokens(system + prompt) + 4000 <= limit:
        data, usage = provider.run(system, prompt, SCHEMA, model)
        return parse(data), usage
    return _run_parts(provider, lesson, model, limit)


def _run_parts(provider, lesson, model, limit):
    budget = max(limit - 4000, 2000) * 3  # characters of cards per part
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
    title, chapters, glossary, card_titles, summaries, total_in, total_out, usage = "", [], {}, [], [], 0, 0, None
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
            part = AiResult.model_validate(_compat(data))
        except ValidationError:
            continue
        title = title or part.lesson_title
        summaries.append(part.lesson_summary)
        chapters += [ch for ch in part.chapters if ch.start_card in ids] or part.chapters[:1]
        for t in part.glossary:
            glossary.setdefault(term_key(t.term), t)
        card_titles += [ct for ct in part.card_titles if ct.card in ids]
    # One short extra call turns the part summaries into a summary of the whole lesson
    summary = " ".join(summaries)
    if len(summaries) > 1:
        try:
            data, extra = provider.run(system, SUMMARY_TASK.format(title=lesson.title, parts="\n".join(f"- {x}" for x in summaries)),
                                       json_schema(AiSummary), model)
            summary = AiSummary.model_validate(data).lesson_summary
            total_in += extra.input_tokens
            total_out += extra.output_tokens
        except (ValidationError, AfterclassError):
            pass
    merged = AiResult(lesson_title=title or lesson.title, lesson_summary=summary, chapters=chapters,
                      glossary=list(glossary.values())[:20],
                      card_titles=card_titles)
    usage.input_tokens, usage.output_tokens = total_in, total_out
    return parse(merged.model_dump()), usage


def term_key(term):
    """'Opaque Predicates', 'opaque predicate' and 'Control Flow Flattening (CFF)' -> one key each."""
    t = re.sub(r"\(.*?\)", "", term).strip().lower()
    return t[:-1] if t.endswith("s") and len(t) > 4 else t


def _compat(data):
    """Results cached by older versions carry extra fields (summaries, exam questions, …): drop them."""
    if not isinstance(data, dict):
        return data
    out = {"lesson_title": data.get("lesson_title", ""), "lesson_summary": data.get("lesson_summary") or "",
           "chapters": [], "glossary": [], "card_titles": []}
    out["chapters"] = [{"start_card": c.get("start_card"), "title": c.get("title")} for c in data.get("chapters", [])
                       if isinstance(c, dict)]
    out["glossary"] = [{k: g.get(k) for k in ("term", "definition", "card")} for g in data.get("glossary", [])
                       if isinstance(g, dict)]
    out["card_titles"] = [{"card": t.get("card"), "title": t.get("title")} for t in data.get("card_titles", [])
                          if isinstance(t, dict)]
    return out


def parse(data):
    try:
        result = AiResult.model_validate(_compat(data))
    except ValidationError as e:
        raise AfterclassError(f"the AI answer does not match the expected format: {e.errors()[0]['msg']}")
    # Valid JSON can still be a placeholder ("could not generate ..."): never cache or show that
    if len(result.chapters) < 2 or not result.glossary:
        raise AfterclassError(f"the AI answer is nearly empty ({len(result.chapters)} chapters, "
                         f"{len(result.glossary)} glossary terms)")
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
    """Use AI chapters and the glossary on the lesson. times: card_times() from the AI call."""
    to_current = _remap(lesson, times)
    chapters, seen = [], set()
    for ch in sorted(result.chapters, key=lambda c: c.start_card):
        card = lesson.cards[to_current(ch.start_card)].id
        if card in seen:
            continue
        seen.add(card)
        chapters.append(Chapter(title=ch.title, card=card, source="ai"))
    # Chapters shorter than 3 minutes merge into the next one (small models like to over-split)
    starts = [lesson.cards[ch.card].start or 0 for ch in chapters] + [lesson.duration or float("inf")]
    chapters = [ch for k, ch in enumerate(chapters) if k == len(chapters) - 1 or starts[k + 1] - starts[k] >= 180]
    if chapters:
        if chapters[0].card != lesson.cards[0].id:
            chapters[0].card = lesson.cards[0].id
        lesson.stats["slide_chapters"] = [{"title": c.title, "card": c.card} for c in lesson.chapters]
        lesson.chapters = chapters
    for ct in result.card_titles:
        title = " ".join(ct.title.split())[:80]
        if title:
            lesson.cards[to_current(ct.card)].quick_title = title
    lesson.ai = {
        "title": result.lesson_title,
        "summary": result.lesson_summary,
        "glossary": [{**t.model_dump(), "card": to_current(t.card)}
                     for t in {term_key(t.term): t for t in reversed(result.glossary)}.values()][::-1],
        **meta,
    }


SCHEMA = json_schema()
