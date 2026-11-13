import numpy as np

from afterclass.model import Card, Segment
from afterclass.steps import analyze, speakers


def _voice(rng, base, noise=0.25):
    v = base + noise * rng.standard_normal(base.shape)
    return v / np.linalg.norm(v)


def test_lecturer_and_students_are_told_apart():
    rng = np.random.default_rng(0)
    lecturer, student = rng.standard_normal(192), rng.standard_normal(192)
    segs, emb = [], []
    t = 0.0
    for i in range(40):  # mostly the lecturer, a student asks around segment 20
        who = student if 20 <= i < 23 else lecturer
        segs.append({"start": t, "end": t + 4.0, "text": f"s{i}"})
        emb.append(_voice(rng, who))
        t += 4.5
    segs.append({"start": t, "end": t + 0.4, "text": "ok"})  # too short for an embedding
    emb.append(None)
    labels = speakers.assign(segs, emb)
    assert labels[:20] == ["A"] * 20
    assert labels[20:23] == ["B"] * 3
    assert labels[-1] == "A"  # inherits the previous label
    summary = speakers.summary(segs, labels)
    assert summary[0]["label"] == "Lecturer" and summary[1]["id"] == "B"


def test_tiny_other_voice_is_folded_into_lecturer():
    rng = np.random.default_rng(1)
    lecturer, other = rng.standard_normal(192), rng.standard_normal(192)
    segs = [{"start": i * 5.0, "end": i * 5.0 + 4.0, "text": "x"} for i in range(20)]
    segs[5]["end"] = segs[5]["start"] + 1.5  # one short odd segment (< MIN_OTHER_SECONDS in total)
    emb = [_voice(rng, other if i == 5 else lecturer) for i in range(20)]
    assert set(speakers.assign(segs, emb)) == {"A"}


def test_paragraphs_break_on_speaker_pause_and_length():
    c = Card(id=0, kind="slide", start=0, end=100)
    c.segments = [
        Segment(0, 2, "First sentence.", "A"),
        Segment(2.1, 4, "Second sentence.", "A"),
        Segment(4.2, 6, "A question?", "B"),       # speaker change
        Segment(6.1, 8, "An answer.", "A"),         # speaker change
        Segment(12, 14, "After a pause.", "A"),     # pause > 1.5 s
    ] + [Segment(14 + i, 15 + i, "One more sentence.", "A") for i in range(6)]
    analyze.mark_paragraphs([c])
    starts = [i for i, s in enumerate(c.segments) if s.para]
    assert starts[:4] == [0, 2, 3, 4]
    assert len(starts) == 5  # the run of short sentences is split once after ~4 sentences


def _discussion(rng, voices, order, seconds=4.0):
    segs, emb, t = [], [], 0.0
    for who in order:
        segs.append({"start": t, "end": t + seconds, "text": who})
        emb.append(_voice(rng, voices[who]))
        t += seconds + 0.5
    return segs, emb


def test_discussion_voices_are_equal_and_named_by_first_words():
    rng = np.random.default_rng(2)
    voices = {k: rng.standard_normal(192) for k in "wxyz"}
    order = list("xxwwzzyyxwzy" * 4)  # four people, nobody dominant
    segs, emb = _discussion(rng, voices, order)
    labels = speakers.assign_discussion(segs, emb)
    first = dict(zip(order[::-1], labels[::-1]))  # the label each voice ended up with
    assert len(set(labels)) == 4 and all(first[w] == lab for w, lab in zip(order, labels))
    assert (first["x"], first["w"], first["z"], first["y"]) == ("A", "B", "C", "D")
    summary = speakers.summary(segs, labels, "discussion")
    assert [s["label"] for s in summary] == ["Speaker A", "Speaker B", "Speaker C", "Speaker D"]


def test_discussion_with_known_number_of_short_turns():
    # short turns (each under MIN_OTHER_SECONDS) must still be grouped into the given number of people
    rng = np.random.default_rng(3)
    voices = {k: rng.standard_normal(192) for k in "abc"}
    order = list("abcacbbacabc" * 3)
    segs, emb = _discussion(rng, voices, order, seconds=2.5)
    labels = speakers.assign_discussion(segs, emb, n=3)
    assert len(set(labels)) == 3
    assert all((labels[i] == labels[j]) == (order[i] == order[j]) for i in range(len(order)) for j in range(len(order)))


def test_discussion_short_island_takes_its_neighbours_label():
    rng = np.random.default_rng(4)
    voices = {k: rng.standard_normal(192) for k in "ab"}
    order = list("aaaabbbbaaaabbbb")
    segs, emb = _discussion(rng, voices, order)
    segs.insert(4, {"start": 17.9, "end": 18.0, "text": "mhm"})  # too short for an embedding
    emb.insert(4, None)
    segs[2]["end"] = segs[2]["start"] + 1.5  # one short "b"-sounding blip inside a's turn
    emb[2] = _voice(rng, voices["b"])
    labels = speakers.assign_discussion(segs, emb)
    assert labels[:5] == ["A"] * 5


def test_speaker_mode_from_old_and_new_values():
    assert [speakers.mode_of(v) for v in (True, False, None, "on", "lecture", "discussion", "off")] == \
        ["lecture", "off", "off", "lecture", "lecture", "discussion", "off"]


def test_sentence_keys_match_the_page():
    # the page uses JavaScript's toFixed(2): exact ties round up
    assert [speakers.key(t) for t in (17.6, 0.125, 2.675, 3)] == ["17.60", "0.13", "2.67", "3.00"]


def test_corrections_resolve_fixes_and_merges():
    corr = {"speakers": {"B": "Mia"}, "speaker_fix": {"4.50": "C"}, "speaker_merge": {"C": "B", "D": "E", "E": "D"}}
    assert speakers.resolve("A", 4.5, corr) == "B"  # moved to C, and C is the same person as B
    assert speakers.resolve("A", 9, corr) == "A"
    assert speakers.resolve("D", 9, corr) in ("D", "E")  # a merge circle never hangs
    assert speakers.display_name("B", corr, [], "discussion") == "Mia"
    assert speakers.display_name("A", speakers.corrections({}), [], "lecture") == "Lecturer"


def test_similar_finds_the_rest_of_a_mixed_in_voice():
    rng = np.random.default_rng(6)
    x, y = rng.standard_normal(192), rng.standard_normal(192)
    order = "xxyxxyxyxxyx"  # one label "B" that really is two people
    voices = {speakers.key(i * 5.0): _voice(rng, x if c == "x" else y) for i, c in enumerate(order)}
    starts = [i * 5.0 for i in range(len(order))]
    moved = [starts[2], starts[5]]  # the student corrects two of y's sentences
    found = speakers.similar(voices, moved, starts)
    assert sorted(found) == sorted(speakers.key(t) for t, c in zip(starts, order) if c == "y" and t not in moved)


def test_split_and_regroup(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("AFTERCLASS_CONFIG", str(tmp_path / "config" / "config.toml"))  # not this Mac's known voices

    rng = np.random.default_rng(7)
    a, b = rng.standard_normal(192), rng.standard_normal(192)
    order = "aabbaabbab"
    segs = [{"start": i * 5.0, "end": i * 5.0 + 4.5, "text": f"s{i}.", "speaker": "A", "para": i == 0} for i in range(len(order))]
    voices = {speakers.key(s["start"]): _voice(rng, a if c == "a" else b) for s, c in zip(segs, order)}
    keep, move = speakers.split(voices, segs)
    assert {len(keep), len(move)} == {5} and len({order[int(float(k) / 5)] for k in move}) == 1

    folder = tmp_path / "lesson"
    (folder / "data").mkdir(parents=True)
    (folder / "notes").mkdir()
    lesson = {"id": "x", "title": "T", "cards": [{"id": 0, "segments": segs[:6]}, {"id": 1, "segments": segs[6:]}],
              "chapters": [], "speakers": [], "speaker_mode": "lecture"}
    (folder / "data" / "lesson.json").write_text(json.dumps(lesson))
    np.savez(folder / "data" / "voices.npz", starts=np.array([s["start"] for s in segs]), ok=np.ones(len(segs), bool),
             emb=np.stack([voices[speakers.key(s["start"])] for s in segs]))
    (folder / "notes" / "notes.json").write_text(json.dumps({"stars": {"1": True}, "speakers": {"A": "Mia"}}))
    summary = speakers.regroup(folder, "discussion", 2)
    out = json.loads((folder / "data" / "lesson.json").read_text())
    labels = [s["speaker"] for c in out["cards"] for s in c["segments"]]
    assert [s["id"] for s in summary] == ["A", "B"] and out["speaker_mode"] == "discussion"
    assert all((x == y) == (order[i] == order[j]) for i, x in enumerate(labels) for j, y in enumerate(labels))
    assert out["cards"][0]["segments"][2]["para"]  # a new voice starts a paragraph
    assert json.loads((folder / "notes" / "notes.json").read_text()) == {"stars": {"1": True}}
    assert json.loads((folder / "notes" / "speakers-before-regroup.json").read_text()) == {"speakers": {"A": "Mia"}}
    assert "Mia" not in (folder / "index.html").read_text()  # the page no longer carries the old names
