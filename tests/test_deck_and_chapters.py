from lecap.model import Card
from lecap.steps import analyze, deck


def page(n, text, title="", divider=()):
    return {"page": n, "title": title, "text": text, "divider": list(divider)}


PAGES = [
    page(1, "Code Obfuscation Techniques Opaque Predicates", divider=["Code Obfuscation Techniques", "Opaque Predicates"]),
    page(2, "Opaque Predicates An opaque predicate is a conditional statement whose truth value is known a priori",
         "Opaque Predicates"),
    page(3, "Opaque Predicates Examples opaque true predicate always evaluates to true", "Opaque Predicates"),
    page(4, "Code Obfuscation Techniques Control Flow Flattening", divider=["Code Obfuscation Techniques", "Control Flow Flattening"]),
    page(5, "Control Flow Flattening dispatcher state variable switch loop", "Control Flow Flattening"),
]


def test_match_by_page_number_and_text():
    frames = [
        (0, {"text": "Page 2 of 5 whatever", "page": 2, "pages": 5}),
        (1, {"text": "Opaque Predicates Examples opaque true predicate always evaluates"}),
        (2, {"text": "Sublime Text File Edit def foo(): return 1", "demo": True}),
        (3, {"text": "Control Flow Flattening dispatcher state variable switch loop"}),
    ]
    m = deck.match(frames, PAGES)
    assert m[0][0] == 2 and m[0][2] == "page number"
    assert m[1][0] == 3
    assert 2 not in m  # code in an editor is not a slide
    assert m[3][0] == 5


def _cards(pages_seq):
    cards = []
    for i, p in enumerate(pages_seq):
        c = Card(id=i, kind="slide", start=i * 60.0, end=(i + 1) * 60.0, deck_page=p)
        c._ocr = {}
        cards.append(c)
    return cards


def test_deck_chapters_running_max():
    # 1 → 2 → jump back to 1 briefly → 5: the jump back must not create a chapter
    cards = _cards([1, 2, 1, 5])
    chapters, source = analyze.chapters_from_deck(cards, PAGES, outline=[])
    assert [(c.title, c.card) for c in chapters] == [("Opaque Predicates", 0), ("Control Flow Flattening", 3)]
    assert source == "deck dividers"


def test_merge_repeats_same_page():
    cards = _cards([2, 2, 3])
    merged = analyze.merge_repeats(cards)
    assert len(merged) == 2 and merged[0].end == 120.0 and [c.id for c in merged] == [0, 1]


def test_breaks_and_segments():
    cards = _cards([1, 2, 3])
    analyze.assign_segments(cards, [{"start": 65, "end": 70, "text": "hi"}])
    analyze.mark_breaks(cards, silences=[[0, 60]])
    assert cards[0].kind == "break"
    assert cards[1].segments[0].text == "hi"
    assert cards[2].kind == "slide"  # silence does not cover it
