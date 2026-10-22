"""Combine scenes, OCR, transcript, silences and the deck into cards and chapters."""

import bisect
import hashlib
from collections import Counter
from difflib import SequenceMatcher

from ..model import Card, Chapter, Segment
from .deck import match as match_deck
from .silence import overlap


def build_cards(scenes, ocr, slides_dir, rel_slides="slides"):
    """One card per scene; identical consecutive frames are merged."""
    cards, last_hash = [], None
    for n, start, end in scenes:
        name = f"{n:03d}"
        img = slides_dir / f"{name}.jpg"
        digest = hashlib.md5(img.read_bytes()).hexdigest() if img.exists() else None
        if cards and digest and digest == last_hash:
            cards[-1].end = end
            continue
        last_hash = digest
        o = ocr.get(name, {})
        kind = "demo" if o.get("demo") else ("slide" if o.get("words", 0) >= 1 else "other")
        title = o.get("title") or " ".join(o.get("divider", []))
        cards.append(Card(id=len(cards), kind=kind, start=start, end=end,
                          image=f"{rel_slides}/{name}.jpg" if img.exists() else None,
                          title=title, text=o.get("text", "")))
        cards[-1]._ocr = o  # transient, not serialized
    return cards


def apply_deck(cards, pages, rel_deck="deck"):
    """Attach matched deck pages: sharp image, exact text and title. Returns the match map."""
    matches = match_deck([(c.id, c._ocr) for c in cards], pages)
    for c in cards:
        if c.id in matches:
            page, _score, _how = matches[c.id]
            p = pages[page - 1]
            c.deck_page = page
            c.deck_image = f"{rel_deck}/{page:03d}.jpg"
            c.text = p["text"]
            c.title = p["title"] or " ".join(p["divider"]) or c.title
            if c.kind != "slide":
                c.kind = "slide"  # e.g. the PDF shown in a viewer window
    return matches


def merge_repeats(cards):
    """Merge consecutive slide cards that show the same slide (same deck page or same text)."""
    out = []
    for c in cards:
        prev = out[-1] if out else None
        if prev and prev.kind == c.kind == "slide" and _same_slide(prev, c):
            prev.end = c.end
            continue
        out.append(c)
    for i, c in enumerate(out):
        c.id = i
    return out


def _same_slide(a, b):
    if a.deck_page and b.deck_page:
        return a.deck_page == b.deck_page
    if len(a.text) < 15 or len(b.text) < 15:
        return False
    return SequenceMatcher(None, a.text, b.text).ratio() >= 0.92


def split_at_silences(cards, silences, min_len=180):
    """Cut cards around long silences (breaks) so the break becomes its own foldable card."""
    long = [s for s in silences if s[1] - s[0] >= min_len]
    out = []
    for c in cards:
        pieces = [c]
        for s0, s1 in long:
            last = pieces[-1]
            if s0 >= last.end or s1 <= last.start or (s0 <= last.start and s1 >= last.end):
                continue
            before = Card(**{**_fields(last), "end": max(s0, last.start)})
            brk = Card(**{**_fields(last), "start": max(s0, last.start), "end": min(s1, last.end), "kind": "break"})
            after = Card(**{**_fields(last), "start": min(s1, last.end)})
            pieces[-1:] = [p for p in (before, brk, after) if p.end - p.start > 1]
        for p in pieces:
            p._ocr = c._ocr
        out.extend(pieces)
    for i, c in enumerate(out):
        c.id = i
    return out


def _fields(card):
    return {k: getattr(card, k) for k in card.__dataclass_fields__ if k != "segments"}


def assign_segments(cards, segments):
    starts = [c.start for c in cards]
    for s in segments:
        i = max(bisect.bisect_right(starts, s["start"]) - 1, 0)
        cards[i].segments.append(Segment(s["start"], s["end"], s["text"]))


def mark_breaks(cards, silences, min_len=60):
    for c in cards:
        if c.kind == "break" and c.segments:  # speech after all: not a break
            c.kind = "demo" if c._ocr.get("demo") else ("slide" if c._ocr.get("words") else "other")
        dur = (c.end or 0) - (c.start or 0)
        if not c.segments and dur >= min_len and overlap(c.start, c.end, silences) >= 0.7 * dur:
            c.kind = "break"


def divider_titles(line_lists):
    """Drop lines shared by several different dividers (the deck title), keep the section name."""
    distinct = {tuple(ls) for ls in line_lists if ls}
    counts = Counter(line for ls in distinct for line in set(ls))
    titles = []
    for ls in line_lists:
        keep = [l for l in ls if counts[l] < 2] or ls
        titles.append(" ".join(keep)[:90])
    return titles


def chapters_from_deck(cards, pages, outline):
    """(page, title) boundaries from the PDF outline if useful, else from divider pages."""
    if len(outline) > 2:
        bounds = [(p, t) for lvl, t, p in outline if lvl <= 2]
        source = "deck outline"
    else:
        divs = [p for p in pages if p["divider"]]
        bounds = list(zip([p["page"] for p in divs], divider_titles([p["divider"] for p in divs])))
        source = "deck dividers"
    if not bounds:
        return [], source
    bound_pages = [b[0] for b in bounds]
    chapters, current = [], -1
    for c in cards:
        if not c.deck_page:
            continue
        idx = bisect.bisect_right(bound_pages, c.deck_page) - 1
        if idx > current:  # running max: a short jump back doesn't split a chapter
            current = idx
            chapters.append(Chapter(title=bounds[idx][1], card=c.id, source="deck"))
    return _dedupe(chapters), source


def chapters_from_video(cards):
    divs = [(c, c._ocr.get("divider", [])) for c in cards if getattr(c, "_ocr", {}).get("divider")]
    titles = divider_titles([d for _, d in divs])
    chapters = []
    for (c, _), title in zip(divs, titles):
        if not chapters or chapters[-1].title.lower() != title.lower():
            chapters.append(Chapter(title=title, card=c.id, source="slides"))
    return _dedupe(chapters), "slide dividers in the video"


def _dedupe(chapters):
    out = []
    for ch in chapters:
        if out and out[-1].title.lower() == ch.title.lower():
            continue
        out.append(ch)
    return out


def with_start(chapters, cards, min_len=30):
    """Drop chapters shorter than min_len seconds (the next one takes over) and make sure
    the first card belongs to a chapter."""
    chapters = [ch for k, ch in enumerate(chapters)
                if k + 1 == len(chapters) or cards[chapters[k + 1].card].start - cards[ch.card].start >= min_len]
    if cards and (not chapters or chapters[0].card != cards[0].id):
        chapters.insert(0, Chapter(title="Beginning", card=cards[0].id, source="auto"))
    return chapters


def lesson_id(name, duration):
    return hashlib.sha1(f"{name}:{round(duration or 0)}".encode()).hexdigest()[:12]
