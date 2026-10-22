"""ffprobe: duration and stream types of the input."""

import json
import subprocess

from ..ui import AfterclassError


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise AfterclassError(f"ffprobe cannot read {path.name}: {r.stderr.strip()}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "has_video": video is not None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
    }
