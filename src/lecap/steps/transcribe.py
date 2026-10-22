"""Speech to text with mlx-whisper (Apple Silicon), plus a filter for Whisper hallucinations."""

import json
import re

from ..ui import LecapError
from .silence import overlap

# Phrases Whisper invents in silence or noise
FILLERS = {
    "thank you.", "thank you", "thanks for watching!", "thank you for watching.", "okay.", "ok.", "bye.",
    "you", "you.", "so.", ".", "...", "vielen dank.", "danke.", "untertitel im auftrag des zdf, 2017",
    "untertitel der amara.org-community",
}


def transcribe(video, model, language, word_timestamps):
    """Returns raw segments [{start, end, text, no_speech_prob, avg_logprob, compression_ratio}]."""
    try:
        import mlx_whisper
    except ImportError as e:
        raise LecapError(f"mlx-whisper is not available ({e}). It needs a Mac with Apple Silicon.")
    kwargs = dict(
        path_or_hf_repo=model,
        language=language,
        # Off: otherwise one hallucinated line is fed back as context and repeats for an hour
        condition_on_previous_text=False,
        verbose=False,  # shows a progress bar
    )
    if word_timestamps:
        kwargs.update(word_timestamps=True, hallucination_silence_threshold=2.0)
    result = mlx_whisper.transcribe(str(video), **kwargs)
    keep = ("start", "end", "text", "no_speech_prob", "avg_logprob", "compression_ratio")
    return [{k: s.get(k) for k in keep} for s in result.get("segments", [])]


def clean(raw, silences):
    """Drop hallucinated segments. Returns (segments, report)."""
    out, dropped, loops = [], {"silence": 0, "filler": 0, "repeat": 0, "empty": 0}, 0
    run_text, run_len = None, 0
    for s in raw:
        text = re.sub(r"\s+", " ", (s.get("text") or "")).strip()
        start, end = float(s["start"]), float(s["end"])
        norm = text.lower()
        if norm == run_text:
            run_len += 1
        else:
            if run_len >= 10:
                loops += 1
            run_text, run_len = norm, 1
        if not text:
            dropped["empty"] += 1
            continue
        dur = max(end - start, 0.01)
        if overlap(start, end, silences) / dur > 0.8:
            dropped["silence"] += 1
            continue
        if norm in FILLERS and (s.get("no_speech_prob") or 0) > 0.3:
            dropped["filler"] += 1
            continue
        if run_len >= 3:  # the same line three times in a row is a loop, keep the first two
            dropped["repeat"] += 1
            continue
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    if run_len >= 10:
        loops += 1
    total = max(len(raw), 1)
    report = {
        "raw": len(raw),
        "kept": len(out),
        "dropped": dropped,
        "dropped_share": round(1 - len(out) / total, 3),
        "loops": loops,
    }
    return out, report


def to_srt(segments):
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"

    return "\n".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text'].strip()}\n"
                     for i, s in enumerate(segments, 1))


def save(raw, json_path, srt_path):
    json_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    srt_path.write_text(to_srt(raw), encoding="utf-8")
