import json

import numpy as np
import pytest

from afterclass import config, voices
from afterclass.steps import speakers


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    """voices.json and the setting go to tmp, not the real config folder."""
    monkeypatch.setenv("AFTERCLASS_CONFIG", str(tmp_path / "config" / "config.toml"))


def turn_on(on=True):
    config.save({**config.load(), "remember_voices": on})


def _voice(rng, base, noise=0.25):
    v = base + noise * rng.standard_normal(base.shape)
    return v / np.linalg.norm(v)


def lesson(folder, rng, people, order, seconds=4.5):
    """A lesson folder whose sentences (one per letter of order, labelled by the letter) sound like people[letter]."""
    segs = [{"start": i * 5.0, "end": i * 5.0 + seconds, "text": f"s{i}.", "speaker": c.upper()} for i, c in enumerate(order)]
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "lesson.json").write_text(json.dumps({"cards": [{"id": 0, "segments": segs}]}))
    emb = [_voice(rng, people[c]) for c in order]
    np.savez(folder / "data" / "voices.npz", starts=np.array([s["start"] for s in segs]), ok=np.ones(len(segs), bool),
             emb=np.stack(emb))
    return folder, segs, emb


def test_nothing_is_remembered_while_off(tmp_path):
    rng = np.random.default_rng(1)
    folder, *_ = lesson(tmp_path / "one", rng, {"a": rng.standard_normal(192)}, "a" * 8)
    assert voices.learn(folder, {"speakers": {"A": "Mia"}}) == []
    assert not (tmp_path / "config" / "voices.json").exists()


def test_named_speakers_are_recognized_in_later_lessons(tmp_path):
    turn_on()
    rng = np.random.default_rng(2)
    people = {c: rng.standard_normal(192) for c in "abc"}
    one, *_ = lesson(tmp_path / "one", rng, people, "aabbaabbaabb")
    # B's sentence 2 is fixed to A on the page; "Speaker B" is the build's label, not a name
    assert voices.learn(one, {"speakers": {"A": "Mia", "B": "Speaker B"}, "speaker_fix": {"10.00": "A"}}) == ["Mia"]
    assert voices.learn(one, {"speakers": {"A": "Mia"}}) == ["Mia"]  # saved again: replaced, not added up
    assert voices.people() == [{"name": "Mia", "lessons": 1}]
    assert voices.learn(one, {"speakers": {"A": "Maya"}}) == ["Maya"]  # renamed: Mia no longer has this lesson
    assert voices.people() == [{"name": "Maya", "lessons": 1}]

    swapped = {"a": people["b"], "b": people["a"], "c": people["c"]}  # here Maya is B, C is someone unknown
    two, segs, emb = lesson(tmp_path / "two", rng, swapped, "bbaabbaabbcc")
    labels = [s["speaker"] for s in segs]
    assert voices.recognize(segs, labels, emb, exclude=two) == {"B": "Maya"}
    assert voices.recognize(segs, labels, emb, exclude=one) == {}  # a lesson is no evidence for itself
    summary = speakers.summary(segs, labels, "discussion")
    assert [s["label"] for s in speakers.with_known(two, summary, segs, labels)] == ["Speaker A", "Maya", "Speaker C"]
    turn_on(False)
    assert speakers.with_known(two, summary, segs, labels) == summary  # off: the library is kept but not used
    assert voices.people() and voices.forget("Maya") == []


def test_short_or_ambiguous_voices_are_not_named(tmp_path):
    turn_on()
    rng = np.random.default_rng(3)
    a = rng.standard_normal(192)
    short, *_ = lesson(tmp_path / "short", rng, {"a": a}, "aaa")  # 13.5 s of speech
    assert voices.learn(short, {"speakers": {"A": "Mia"}}) == []
    one, *_ = lesson(tmp_path / "one", rng, {"a": a}, "a" * 8)
    twin, *_ = lesson(tmp_path / "twin", rng, {"a": a}, "a" * 8)  # the same voice under two names
    voices.learn(one, {"speakers": {"A": "Mia"}})
    voices.learn(twin, {"speakers": {"A": "Lena"}})
    three, segs, emb = lesson(tmp_path / "three", rng, {"a": a}, "a" * 8)
    assert voices.recognize(segs, [s["speaker"] for s in segs], emb) == {}  # too close to call
    assert voices.forget() == [] and not (tmp_path / "config" / "voices.json").exists()


def test_a_speaker_can_be_picked_from_the_known_people(tmp_path):
    from afterclass import server

    rng = np.random.default_rng(4)
    people = {c: rng.standard_normal(192) for c in "ab"}
    one, *_ = lesson(tmp_path / "one", rng, people, "aaaabbbb" * 2)
    two, segs, _ = lesson(tmp_path / "two", rng, {"a": people["b"]}, "a" * 8)  # Lena talks in lesson two
    app = server.App(two)
    starts = [s["start"] for s in segs]
    assert server.speakers_payload(app, "known", {"starts": starts}) == {"enabled": False, "people": []}  # off
    turn_on()
    voices.learn(one, {"speakers": {"A": "Mia", "B": "Lena"}})
    got = server.speakers_payload(app, "known", {"starts": starts})["people"]
    assert [p["name"] for p in got] == ["Lena", "Mia"] and got[0]["likely"] and not got[1]["likely"]
    # without fingerprints (an old lesson): everyone, by name
    assert [p["name"] for p in voices.ranked([])] == ["Lena", "Mia"]


def test_rename_a_known_person_in_all_their_lessons(tmp_path):
    turn_on()
    rng = np.random.default_rng(6)
    people = {c: rng.standard_normal(192) for c in "ab"}
    folders = []
    for name, order in (("one", "aaaaabbbbb"), ("two", "bbbbbaaaaa")):
        folder, *_ = lesson(tmp_path / name, rng, people, order)
        (folder / "notes").mkdir()
        notes = {"stars": {"1": True}, "speakers": {"A": "huber", "B": "Mia"}}
        (folder / "notes" / "notes.json").write_text(json.dumps(notes))
        voices.learn(folder, notes)
        folders.append(folder)
    voices.learn(tmp_path / "two", {"speakers": {"A": "Huber", "B": "Mia"}})  # same person, other spelling
    assert voices.lessons_of("HUBER", exclude=folders[0]) == [str(folders[1].resolve())]
    changed = voices.rename("Huber", "Prof. Huber")
    assert sorted(changed) == sorted(str(f.resolve()) for f in folders)
    assert voices.people() == [{"name": "Mia", "lessons": 2}, {"name": "Prof. Huber", "lessons": 2}]
    for f in folders:
        notes = json.loads((f / "notes" / "notes.json").read_text())
        assert notes == {"stars": {"1": True}, "speakers": {"A": "Prof. Huber", "B": "Mia"}}  # the rest untouched
    voices.rename("Mia", "prof. huber")  # into an existing person: joined
    assert voices.people() == [{"name": "Prof. Huber", "lessons": 2}]
