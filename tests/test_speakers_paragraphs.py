import numpy as np

from lecap.model import Card, Segment
from lecap.steps import analyze, speakers


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
