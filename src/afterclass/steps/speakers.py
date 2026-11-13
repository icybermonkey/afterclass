"""Tell speakers apart, offline (SpeechBrain ECAPA voice embeddings).

Experimental. Two modes:
- lecture: the dominant voice is the lecturer ("A"); other voices are grouped into B, C, …
- discussion: every voice is equal (team work, meetings); voices are clustered and named A, B, C, …
  in order of their first words.
Each transcript segment gets one label; very short segments inherit their neighbour's.
"""

import subprocess
from pathlib import Path

from .. import APP_NAME
from ..ui import AfterclassError

MODEL = "speechbrain/spkrec-ecapa-voxceleb"
RATE = 16000
MIN_LEN = 1.0  # seconds of speech needed for a reliable voice embedding
MAX_LEN = 6.0  # longer segments are cropped around their middle
LECTURER_SIM = 0.40  # cosine similarity to the lecturer voice below which a segment is "someone else"
SAME_OTHER_SIM = 0.55  # other voices closer than this are the same person
MIN_OTHER_SECONDS = 4.0  # an "other" voice with less speech in total is folded back
LECTURER_CLUSTER_SIM = 0.32  # (calibrated on a real lecture: lecturer groups 0.33-0.39, students <= 0.31) a whole "other" group this close to the lecturer is the lecturer (other mic/noise)
DISCUSSION_MERGE_SIM = 0.33  # discussion: voice groups whose centroids are closer than this are one person (from the lecture calibration above; --num-speakers is more reliable)
ISLAND_MAX = 2.0  # discussion: a single segment this short between two of the same voice takes their label
KNOWN_SIM = 0.55  # known voices: a speaker's average voice this close to a person named in another lesson is them (to calibrate)
KNOWN_MARGIN = 0.05  # … and this much closer than to the next known person
KNOWN_MIN_SECONDS = 20.0  # speech needed to remember or recognize a voice

MODES = ("lecture", "discussion", "off")


def mode_of(value):
    """Speaker mode from a config/CLI value; old configs stored true/false."""
    if value is True or str(value).lower() in ("1", "true", "yes", "on", "lecture"):
        return "lecture"
    if value is False or value is None or str(value).lower() in ("0", "false", "no", "off", ""):
        return "off"
    if str(value).lower() == "discussion":
        return "discussion"
    raise ValueError(f"speakers must be one of: {', '.join(MODES)}")


def model_dir():
    return Path.home() / ".cache" / APP_NAME / "models" / "spkrec-ecapa-voxceleb"


def load_audio(video):
    import numpy as np

    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(RATE),
                        "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0:
        raise AfterclassError(f"ffmpeg could not read the audio: {r.stderr.decode()[-200:]}")
    return np.frombuffer(r.stdout, dtype=np.int16)


def embed(audio, segments, progress=None, batch=32):
    """L2-normalised embeddings for segments >= MIN_LEN; None for the rest."""
    import numpy as np

    try:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
    except ImportError as e:
        raise AfterclassError(f"speaker detection needs SpeechBrain ({e}). Reinstall afterclass: "
                         f"uv tool install --reinstall --editable <repo>")

    # The Apple GPU is ~40x faster than the CPU here (seconds instead of a quarter hour)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        model = EncoderClassifier.from_hparams(source=MODEL, savedir=str(model_dir()), run_opts={"device": device})
    except Exception as e:
        raise AfterclassError(f"could not load the speaker model ({e}). It is downloaded once (~80 MB), "
                         "so the first run needs internet.")
    clips, idx = [], []
    for i, s in enumerate(segments):
        dur = s["end"] - s["start"]
        if dur < MIN_LEN:
            continue
        mid = (s["start"] + s["end"]) / 2
        a = max(s["start"], mid - MAX_LEN / 2)
        b = min(s["end"], mid + MAX_LEN / 2)
        clip = audio[int(a * RATE):int(b * RATE)]
        if len(clip) >= MIN_LEN * RATE * 0.9:
            clips.append(clip.astype(np.float32) / 32768.0)
            idx.append(i)
    out = [None] * len(segments)
    for k in range(0, len(clips), batch):
        chunk = clips[k:k + batch]
        longest = max(len(c) for c in chunk)
        wavs = torch.zeros(len(chunk), longest)
        for j, c in enumerate(chunk):
            wavs[j, :len(c)] = torch.from_numpy(c)
        lens = torch.tensor([len(c) / longest for c in chunk])
        with torch.no_grad():
            e = model.encode_batch(wavs.to(device), lens.to(device)).squeeze(1).cpu().numpy()
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        for j, vec in enumerate(e):
            out[idx[k + j]] = vec
        if progress:
            progress(min(k + batch, len(clips)), len(clips))
    return out


def assign(segments, embeddings):
    """Labels per segment ("A" lecturer, "B", …) from embeddings. Pure numpy, testable."""
    import numpy as np

    have = [i for i, e in enumerate(embeddings) if e is not None]
    if not have:
        return [""] * len(segments)
    E = np.stack([embeddings[i] for i in have])
    w = np.array([segments[i]["end"] - segments[i]["start"] for i in have])

    # Lecturer = the dominant voice: start from the duration-weighted mean, then refine on the closest half
    centre = (E * w[:, None]).sum(0)
    for _ in range(4):
        centre /= np.linalg.norm(centre) + 1e-9
        sims = E @ centre
        core = sims >= np.median(sims)
        centre = (E[core] * w[core, None]).sum(0)
    centre /= np.linalg.norm(centre) + 1e-9
    sims = E @ centre

    labels = {}
    others = []  # [(centroid, total seconds, [indices])]
    for k, i in enumerate(have):
        if sims[k] >= LECTURER_SIM:
            labels[i] = "A"
            continue
        best, best_sim = None, SAME_OTHER_SIM
        for g, (c, _, _) in enumerate(others):
            s = float(E[k] @ (c / (np.linalg.norm(c) + 1e-9)))
            if s > best_sim:
                best, best_sim = g, s
        if best is None:
            others.append([E[k] * w[k], w[k], [i]])
        else:
            others[best][0] = others[best][0] + E[k] * w[k]
            others[best][1] += w[k]
            others[best][2].append(i)

    # Name the other voices by how much they speak. Tiny groups, and groups that sound like the
    # lecturer on average (e.g. while screen sharing changes the audio), are folded back to the lecturer
    others.sort(key=lambda o: -o[1])
    letters = iter("BCDEFGHIJKLMNOPQRSTUVWXYZ")
    for c, total, members in others:
        like_lecturer = float((c / (np.linalg.norm(c) + 1e-9)) @ centre) >= LECTURER_CLUSTER_SIM
        name = "A" if total < MIN_OTHER_SECONDS or like_lecturer else next(letters, "Z")
        for i in members:
            labels[i] = name

    return _fill(segments, labels)


def assign_discussion(segments, embeddings, n=None):
    """Labels per segment for a discussion: equal voices, A, B, … by first appearance. n = known number of
    speakers (optional). Pure numpy, testable."""
    import numpy as np

    have = [i for i, e in enumerate(embeddings) if e is not None]
    if not have:
        return [""] * len(segments)
    E = np.stack([embeddings[i] for i in have])
    w = np.array([segments[i]["end"] - segments[i]["start"] for i in have])
    unit = lambda v: v / (np.linalg.norm(v) + 1e-9)

    # Small groups of near-identical segments first (keeps the merging below fast on long recordings)
    groups = []  # [summed weighted embedding, total seconds, [positions in have]]
    for k in range(len(have)):
        sims = [float(E[k] @ unit(g[0])) for g in groups]
        best = int(np.argmax(sims)) if sims else None
        if best is not None and sims[best] > SAME_OTHER_SIM:
            groups[best][0] = groups[best][0] + E[k] * w[k]
            groups[best][1] += w[k]
            groups[best][2].append(k)
        else:
            groups.append([E[k] * w[k], w[k], [k]])

    # Then merge the closest groups (centroid linkage): down to n real voices, or while they sound alike.
    # With n, leftover short groups (coughs, noise) don't count: they are folded in below
    def is_big(g):
        return g[1] >= MIN_OTHER_SECONDS

    while len(groups) > (n or 1):
        C = np.stack([unit(g[0]) for g in groups])
        S = C @ C.T
        np.fill_diagonal(S, -2)
        a, b = divmod(int(np.argmax(S)), len(groups))
        if n:
            if is_big(groups[a]) and is_big(groups[b]) and sum(map(is_big, groups)) <= n:
                break
        elif S[a, b] < DISCUSSION_MERGE_SIM:
            break
        groups[a] = [groups[a][0] + groups[b][0], groups[a][1] + groups[b][1], groups[a][2] + groups[b][2]]
        del groups[b]

    # Voices with too little speech in total join the closest real voice
    keep = [g for g in groups if g[1] >= MIN_OTHER_SECONDS] or [max(groups, key=lambda g: g[1])]
    for g in groups:
        if not any(g is x for x in keep):
            target = max(keep, key=lambda x: float(unit(g[0]) @ unit(x[0])))
            target[2].extend(g[2])

    # Name the voices in order of their first words
    keep.sort(key=lambda g: min(g[2]))
    labels = {}
    for g, name in zip(keep, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        for k in g[2]:
            labels[have[k]] = name
    out = _fill(segments, labels)

    # A single short segment between two of the same voice is most likely that voice, too
    for i in range(1, len(out) - 1):
        if (out[i - 1] == out[i + 1] != out[i]
                and segments[i]["end"] - segments[i]["start"] <= ISLAND_MAX):
            out[i] = out[i - 1]
    return out


def _fill(segments, labels):
    """Segments without an embedding inherit the previous label (or the next one at the start)."""
    out, last = [], None
    for i in range(len(segments)):
        last = labels.get(i, last)
        out.append(last)
    first = next((x for x in out if x), "A")
    return [x or first for x in out]


def summary(segments, labels, mode="lecture"):
    minutes = {}
    for s, lab in zip(segments, labels):
        minutes[lab] = minutes.get(lab, 0) + (s["end"] - s["start"]) / 60
    return [{"id": k, "label": "Lecturer" if k == "A" and mode == "lecture" else f"Speaker {k}", "minutes": round(v, 1)}
            for k, v in sorted(minutes.items())]


def with_known(folder, summary, segments, labels, emb=None):
    """The summary with known people's names as labels ({"known": true}), when the setting `remember_voices` is
    on. `emb` defaults to the lesson's stored fingerprints."""
    from .. import voices

    if not voices.enabled():
        return summary
    if emb is None:
        try:
            stored = load_voices(folder)
        except AfterclassError:
            return summary
        emb = [stored.get(key(s["start"])) for s in segments]
    names = voices.recognize(segments, labels, emb, exclude=folder)
    return [{**s, "label": names[s["id"]], "known": True} if s["id"] in names else s for s in summary]


# --- corrections from the page ---------------------------------------------
# The build's labels stay in data/; what the student corrects on the page lives in notes/notes.json:
# speakers {id: name}, speaker_fix {"<start>": id} (one sentence), speaker_merge {id: other id}.

SIMILAR_MARGIN = 0.05  # a sentence moves along when it is this much closer to the moved ones than to the rest


def key(start):
    """A sentence's id in speaker_fix: its start time (stable across rebuilds unless the transcript is redone).
    Rounded like JavaScript's toFixed(2) on the page (ties up), not like Python's format (ties to even)."""
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(float(start)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def corrections(notes):
    """The speaker part of a notes dict (or of notes/notes.json in a lesson folder)."""
    if isinstance(notes, Path):
        import json

        path = notes / "notes" / "notes.json"
        try:
            notes = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            notes = {}
    return {k: dict(notes.get(k) or {}) for k in ("speakers", "speaker_fix", "speaker_merge")}


def resolve(sid, start, corr):
    """The speaker of a sentence after the page's corrections: a fixed sentence, then merges."""
    sid = corr["speaker_fix"].get(key(start), sid)
    seen = set()
    while sid in corr["speaker_merge"] and sid not in seen:  # merges can chain; never loop
        seen.add(sid)
        sid = corr["speaker_merge"][sid]
    return sid


def display_name(sid, corr, built, mode):
    """Renamed on the page, else the build's label, else Lecturer / Speaker X."""
    if not sid:
        return ""
    label = next((s.get("label") for s in built or [] if s.get("id") == sid), None)
    return corr["speakers"].get(sid) or label or ("Lecturer" if sid == "A" and mode == "lecture" else f"Speaker {sid}")


def load_voices(folder):
    """{key(start): fingerprint} from data/voices.npz, written by the build."""
    import numpy as np

    path = Path(folder) / "data" / "voices.npz"
    if not path.exists():
        raise AfterclassError("this lesson has no stored voice fingerprints: rebuild it once with this AfterClass "
                              "(fast, everything else is cached)")
    v = np.load(path)
    return {key(s): e for s, e, ok in zip(v["starts"], v["emb"], v["ok"]) if ok}


def similar(voices, moved, candidates):
    """Of the candidates (sentences still with the moved sentences' old speaker), the ones that sound like the
    moved sentences rather than like the rest. Two rounds, so the picture sharpens. Returns keys."""
    import numpy as np

    unit = lambda v: v / (np.linalg.norm(v) + 1e-9)
    target = [voices[k] for k in map(key, moved) if k in voices]
    rest = [k for k in map(key, candidates) if k in voices and k not in set(map(key, moved))]
    if not target or not rest:
        return []
    picked = []
    for _ in range(2):
        t = unit(np.sum(target + [voices[k] for k in picked], axis=0))
        stay = [k for k in rest if k not in picked]
        if not stay:
            break
        r = unit(np.sum([voices[k] for k in stay], axis=0))
        new = [k for k in stay if float(voices[k] @ t) - float(voices[k] @ r) > SIMILAR_MARGIN]
        if not new:
            break
        picked += new
    return picked


def split(voices, segments):
    """One speaker's sentences in two voices: (bigger group, smaller group) as keys; the second may be empty."""
    have = [s for s in segments if key(s["start"]) in voices]
    if len(have) < 2:
        return [key(s["start"]) for s in segments], []
    labels = assign_discussion(have, [voices[key(s["start"])] for s in have], n=2)
    groups = {}
    for s, lab in zip(have, labels):
        groups.setdefault(lab, []).append(key(s["start"]))
    a, b = (sorted(groups.values(), key=len, reverse=True) + [[]])[:2]
    return a + [key(s["start"]) for s in segments if key(s["start"]) not in voices], b


def regroup(folder, mode, n=None):
    """Tell the speakers apart again from the stored fingerprints (seconds, no audio needed): relabels
    data/lesson.json, re-marks paragraphs and rewrites the page. The page's speaker corrections no longer fit
    the new labels: they move to notes/speakers-before-regroup.json."""
    import json
    from types import SimpleNamespace

    from . import analyze, render

    folder = Path(folder)
    if mode not in ("lecture", "discussion"):
        raise ValueError("mode must be lecture or discussion")
    voices = load_voices(folder)
    lesson_path = folder / "data" / "lesson.json"
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    segs = [s for c in lesson["cards"] for s in c["segments"]]
    emb = [voices.get(key(s["start"])) for s in segs]
    labels = assign(segs, emb) if mode == "lecture" else assign_discussion(segs, emb, n)
    for s, lab in zip(segs, labels):
        s["speaker"] = lab
    cards = [SimpleNamespace(segments=[SimpleNamespace(**s) for s in c["segments"]]) for c in lesson["cards"]]
    analyze.mark_paragraphs(cards)
    for c, nc in zip(lesson["cards"], cards):
        for s, ns in zip(c["segments"], nc.segments):
            s["para"] = ns.para
    built = summary(segs, labels, mode)
    lesson["speakers"] = with_known(folder, built, segs, labels, emb)
    lesson["speaker_mode"] = mode
    (folder / "data" / "speakers.json").write_text(json.dumps({"starts": [s["start"] for s in segs], "labels": labels,
                                                               "summary": built, "mode": mode, "n": n}))
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=1), encoding="utf-8")

    # First the notes, then the page: the page embeds the notes
    notes_path = folder / "notes" / "notes.json"
    if notes_path.exists():
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
        old = {k: notes.pop(k) for k in ("speakers", "speaker_fix", "speaker_merge") if notes.get(k)}
        if old:
            (folder / "notes" / "speakers-before-regroup.json").write_text(json.dumps(old, ensure_ascii=False, indent=1))
            notes_path.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
    render.refresh(folder)
    return lesson["speakers"]
