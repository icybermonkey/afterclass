"""Known voices (opt-in, setting `remember_voices`, off by default): people named on a lesson page are recognized
in later lessons.

The library is voices.json next to config.toml, on this Mac only: {name: {"lessons": {folder: {"emb", "seconds"}}}}.
One entry per person and lesson (the average voice fingerprint of their sentences there), so saving a lesson's
names again replaces its entries instead of adding up. Nothing is collected while the setting is off, and
turning it off keeps the library: Forget (in the settings) deletes.
"""

import json
import re
from pathlib import Path

from . import config
from .steps import speakers

LIBRARY = "voices.json"
DEFAULT_NAME = re.compile(r"^(lecturer|speaker [a-z])$", re.I)  # the build's own labels are not names


def enabled():
    return bool(config.load().get("remember_voices"))


def _path():
    return config.config_path().parent / LIBRARY


def _load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(lib):
    from .server import atomic_write

    if lib:
        atomic_write(_path(), json.dumps(lib, ensure_ascii=False))
    else:
        _path().unlink(missing_ok=True)


def _unit(v):
    import numpy as np

    return v / (np.linalg.norm(v) + 1e-9)


def _find(lib, name):
    """The library's spelling of a name (names match case-insensitively)."""
    return next((k for k in lib if k.casefold() == name.casefold()), name)


def learn(folder, notes):
    """Remember the voices of the speakers named in a lesson (notes as saved by the page). Returns the names
    remembered. Does nothing unless the setting is on."""
    import numpy as np

    if not enabled():
        return []
    folder = Path(folder).resolve()
    corr = speakers.corrections(notes)
    names = {sid: n.strip() for sid, n in corr["speakers"].items() if n.strip() and not DEFAULT_NAME.match(n.strip())}
    lib = _load()
    for person in lib.values():  # this lesson's entries are replaced (a speaker may have been renamed)
        person.get("lessons", {}).pop(str(folder), None)
    lib = {k: v for k, v in lib.items() if v.get("lessons")}
    done = []
    if names:
        voices = speakers.load_voices(folder)
        lesson = json.loads((folder / "data" / "lesson.json").read_text(encoding="utf-8"))
        per = {}  # speaker id -> ([fingerprints], seconds)
        for c in lesson["cards"]:
            for s in c["segments"]:
                sid = speakers.resolve(s.get("speaker"), s["start"], corr)
                e = voices.get(speakers.key(s["start"]))
                if sid in names and e is not None:
                    embs, secs = per.setdefault(sid, ([], [0.0]))
                    embs.append(e)
                    secs[0] += s["end"] - s["start"]
        for sid, (embs, secs) in per.items():
            if secs[0] < speakers.KNOWN_MIN_SECONDS:
                continue
            name = _find(lib, names[sid])
            lib.setdefault(name, {"lessons": {}})["lessons"][str(folder)] = {
                "emb": [round(float(x), 5) for x in _unit(np.mean(embs, axis=0))], "seconds": round(secs[0], 1)}
            done.append(name)
    _save(lib)
    return done


def recognize(segments, labels, embeddings, exclude=None):
    """{label: name} for the speakers of a lesson that sound like a known person (each person at most once).
    `exclude` is the lesson's own folder: its entries don't count as evidence for itself."""
    import numpy as np

    exclude = str(Path(exclude).resolve()) if exclude else None
    people = {}
    for name, person in _load().items():
        ls = [x for f, x in person.get("lessons", {}).items() if f != exclude]
        if ls:
            people[name] = _unit(np.sum([np.asarray(x["emb"]) * x["seconds"] for x in ls], axis=0))
    if not people:
        return {}
    per = {}
    for s, lab, e in zip(segments, labels, embeddings):
        if e is not None:
            embs, secs = per.setdefault(lab, ([], [0.0]))
            embs.append(e)
            secs[0] += s["end"] - s["start"]
    groups = {lab: _unit(np.mean(embs, axis=0)) for lab, (embs, secs) in per.items()
              if secs[0] >= speakers.KNOWN_MIN_SECONDS}
    names = list(people)
    pairs = []
    for lab, v in groups.items():
        sims = sorted(((float(v @ people[n]), n) for n in names), reverse=True)
        best, name = sims[0]
        second = sims[1][0] if len(sims) > 1 else -1.0
        if best >= speakers.KNOWN_SIM and best - second >= speakers.KNOWN_MARGIN:
            pairs.append((best, lab, name))
    out, taken = {}, set()
    for _, lab, name in sorted(pairs, reverse=True):  # the closest match wins a person
        if name not in taken:
            out[lab] = name
            taken.add(name)
    return out


def people():
    """[{name, lessons}] for the settings window."""
    return [{"name": n, "lessons": len(p.get("lessons", {}))} for n, p in sorted(_load().items(), key=lambda x: x[0].casefold())]


def forget(name=None):
    """Forget one person, or everyone (name None)."""
    lib = {} if name is None else {k: v for k, v in _load().items() if k != name}
    _save(lib)
    return people()
