"""Tell the lecturer and other speakers apart, offline (SpeechBrain ECAPA voice embeddings).

Experimental: the dominant voice is the lecturer ("A"); other voices are grouped into B, C, …
Each transcript segment gets one label; very short segments inherit their neighbour's.
"""

import subprocess
from pathlib import Path

from .. import APP_NAME
from ..ui import LecapError

MODEL = "speechbrain/spkrec-ecapa-voxceleb"
RATE = 16000
MIN_LEN = 1.0  # seconds of speech needed for a reliable voice embedding
MAX_LEN = 6.0  # longer segments are cropped around their middle
LECTURER_SIM = 0.40  # cosine similarity to the lecturer voice below which a segment is "someone else"
SAME_OTHER_SIM = 0.55  # other voices closer than this are the same person
MIN_OTHER_SECONDS = 4.0  # an "other" voice with less speech in total is folded back
LECTURER_CLUSTER_SIM = 0.32  # (calibrated on a real lecture: lecturer groups 0.33-0.39, students <= 0.31) a whole "other" group this close to the lecturer is the lecturer (other mic/noise)


def model_dir():
    return Path.home() / ".cache" / APP_NAME / "models" / "spkrec-ecapa-voxceleb"


def load_audio(video):
    import numpy as np

    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(RATE),
                        "-f", "s16le", "-"], capture_output=True)
    if r.returncode != 0:
        raise LecapError(f"ffmpeg could not read the audio: {r.stderr.decode()[-200:]}")
    return np.frombuffer(r.stdout, dtype=np.int16)


def embed(audio, segments, progress=None, batch=32):
    """L2-normalised embeddings for segments >= MIN_LEN; None for the rest."""
    import numpy as np
    import torch
    from speechbrain.inference.speaker import EncoderClassifier

    # The Apple GPU is ~40x faster than the CPU here (seconds instead of a quarter hour)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:
        model = EncoderClassifier.from_hparams(source=MODEL, savedir=str(model_dir()), run_opts={"device": device})
    except Exception as e:
        raise LecapError(f"could not load the speaker model ({e}). It is downloaded once (~80 MB), "
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

    # Segments without an embedding inherit the previous label (or the next one at the start)
    out, last = [], None
    for i in range(len(segments)):
        last = labels.get(i, last)
        out.append(last)
    first = next((x for x in out if x), "A")
    return [x or first for x in out]


def summary(segments, labels):
    minutes = {}
    for s, lab in zip(segments, labels):
        minutes[lab] = minutes.get(lab, 0) + (s["end"] - s["start"]) / 60
    return [{"id": k, "label": "Lecturer" if k == "A" else f"Speaker {k}", "minutes": round(v, 1)}
            for k, v in sorted(minutes.items())]
