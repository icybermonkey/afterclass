"""Find silent stretches with ffmpeg silencedetect (breaks, recording before class starts)."""

import re
import subprocess

_START = re.compile(r"silence_start: (-?[\d.]+)")
_END = re.compile(r"silence_end: ([\d.]+)")


def detect(video, duration, noise_db=-50, min_len=30):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn",
         "-af", f"silencedetect=n={noise_db}dB:d={min_len}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return parse(r.stderr, duration)


def parse(log, duration):
    out, start = [], None
    for line in log.splitlines():
        if m := _START.search(line):
            start = max(0.0, float(m.group(1)))
        elif (m := _END.search(line)) and start is not None:
            out.append([round(start, 2), round(float(m.group(1)), 2)])
            start = None
    if start is not None:  # silent until the end
        out.append([round(start, 2), round(duration, 2)])
    return out


def overlap(a0, a1, silences):
    """Seconds of [a0, a1] covered by silence."""
    return sum(max(0.0, min(a1, s1) - max(a0, s0)) for s0, s1 in silences)
