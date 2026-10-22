"""Scene (slide change) detection with PySceneDetect, one frame per scene."""

import csv
import shutil
import sys
from pathlib import Path

from ..ui import LecapError, run


def scenedetect_bin():
    # Prefer the copy installed next to lecap (uv tool venv), then PATH
    local = Path(sys.executable).parent / "scenedetect"
    if local.exists():
        return str(local)
    found = shutil.which("scenedetect")
    if not found:
        raise LecapError("scenedetect not found (it is a lecap dependency; reinstall lecap)")
    return found


def detect(video, data_dir, slides_dir, threshold):
    """Writes data/scenes.csv and slides/NNN.jpg."""
    if slides_dir.exists():
        shutil.rmtree(slides_dir)
    (data_dir / "scenes.csv").unlink(missing_ok=True)
    done_marker(data_dir).unlink(missing_ok=True)
    run([
        scenedetect_bin(), "-v", "warning", "-i", str(video), "-o", str(data_dir),
        "detect-adaptive", "--threshold", str(threshold),
        "list-scenes", "-q", "-f", "scenes",
        "save-images", "-n", "1", "-q", "85", "-o", str(slides_dir), "-f", "$SCENE_NUMBER",
    ])
    # scenes.csv is written before the images, so only this marker means "complete"
    done_marker(data_dir).touch()


def done_marker(data_dir):
    return data_dir / ".scenes.done"


def parse_csv(path):
    """[(scene_number, start, end)] from a scenedetect CSV (with or without the timecode row)."""
    rows = list(csv.reader(Path(path).open(encoding="utf-8")))
    try:
        head = next(i for i, r in enumerate(rows) if r and r[0] == "Scene Number")
    except StopIteration:
        raise LecapError(f"unexpected scene list format: {path}")
    header = rows[head]
    scenes = []
    for r in rows[head + 1:]:
        if not r:
            continue
        d = dict(zip(header, r))
        scenes.append((int(d["Scene Number"]), float(d["Start Time (seconds)"]), float(d["End Time (seconds)"])))
    return scenes
