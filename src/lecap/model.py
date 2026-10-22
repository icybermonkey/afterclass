"""Source-neutral lesson model. Every source fills it; the HTML only reads it.

Optional fields (start, image, media, ...) stay None for sources that lack them,
e.g. a future PDF-only source has no times and no player.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""  # "A" = lecturer, "B", "C", … = others; "" = unknown
    para: bool = False  # starts a new paragraph


@dataclass
class Card:
    id: int
    kind: str  # slide | demo | blank | break
    start: float | None = None
    end: float | None = None
    image: str | None = None  # video frame, relative to the output folder
    deck_image: str | None = None  # matched deck page render
    deck_page: int | None = None
    title: str = ""
    text: str = ""  # slide text (deck text if matched, else OCR)
    segments: list[Segment] = field(default_factory=list)


@dataclass
class Chapter:
    title: str
    card: int  # id of the first card
    source: str = "slides"  # slides | deck | ai
    image: str | None = None  # one picture for the whole chapter
    subs: list[dict] = field(default_factory=list)  # subchapters: [{title, card}], text only


@dataclass
class Lesson:
    id: str
    title: str
    source: str  # video (later: audio, pdf, pptx)
    duration: float | None = None
    media: str | None = None  # relative path of the player file
    deck: str | None = None  # relative path of the deck PDF
    deck_pages: int | None = None
    language: str = "en"
    cards: list[Card] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    silences: list[list[float]] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)  # [{id, label, minutes}]
    transcript_view: str = "paragraphs"  # default view on the page
    ai: dict | None = None  # glossary, exam questions, summary, provider info
    stats: dict = field(default_factory=dict)

    def to_json(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    def save(self, path):
        Path(path).write_text(self.to_json(), encoding="utf-8")
