from afterclass.model import Card
from afterclass.steps import analyze, deck


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


def test_long_silence_inside_a_card_becomes_a_break():
    c = Card(id=0, kind="demo", start=0.0, end=1000.0)
    c._ocr = {"demo": True}
    cards = analyze.split_at_silences([c], silences=[[300.0, 900.0], [950.0, 960.0]])
    assert [(p.kind, p.start, p.end) for p in cards] == [("demo", 0.0, 300.0), ("break", 300.0, 900.0), ("demo", 900.0, 1000.0)]
    analyze.assign_segments(cards, [{"start": 10, "end": 12, "text": "a"}, {"start": 905, "end": 910, "text": "b"}])
    analyze.mark_breaks(cards, [[300.0, 900.0]])
    assert cards[1].kind == "break" and not cards[1].segments


def test_chapter_picture_and_text_subchapters():
    from afterclass.model import Chapter

    def card(i, kind, title, deck=None, page=None, length=10):
        return Card(id=i, kind=kind, start=i * 100.0, end=i * 100.0 + length, title=title,
                    image=f"slides/{i:03d}.jpg", deck_image=deck, deck_page=page)

    cards = [card(0, "slide", "Code Obfuscation Techniques Opaque Predicates", deck="deck/045.jpg", page=45),
             card(1, "slide", "Opaque Predicates"), card(2, "slide", "Examples"),
             card(3, "demo", "", length=100), card(4, "demo", "", length=200),   # one long demo run
             card(5, "slide", "Examples"),                                        # repeated title
             card(6, "demo", "", length=20),                                      # short demo: skipped
             card(7, "slide", "} VM Entry"), card(8, "slide", "handle_vpush handle_vadd"),
             card(9, "break", ""), card(10, "slide", "Control Flow Flattening")]
    chapters = analyze.decorate_chapters([Chapter("Opaque Predicates", 0), Chapter("CFF", 10)], cards, {45})
    assert chapters[0].image == "deck/045.jpg"
    assert [(s["title"], s["card"]) for s in chapters[0].subs] == [("Examples", 2), ("Live demo", 3)]
    assert chapters[1].image == "slides/010.jpg"
    assert [s["title"] for s in chapters[1].subs] == ["Control Flow Flattening"]
