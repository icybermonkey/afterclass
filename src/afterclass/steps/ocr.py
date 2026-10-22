"""OCR of slide frames with tesseract: text, title, "Page X of N", section dividers, live demos."""

import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from ..ui import AfterclassError

# Whisper language code -> tesseract traineddata
TESS_LANG = {"en": "eng", "de": "deu", "fr": "fra", "it": "ita", "es": "spa", "nl": "nld", "pt": "por"}

# macOS menu bar entries: several of them at the very top means a screen capture of an app
MENU_WORDS = {
    "file", "edit", "view", "window", "help", "go", "history", "bookmarks", "selection", "find", "goto",
    "tools", "project", "shell", "session", "scripts", "profiles", "analysis", "debugger", "plugins",
    "format", "insert", "tab",
}

# One-word slides that still start a section
DIVIDER_WORDS = {
    "introduction", "conclusion", "summary", "outlook", "agenda", "outline", "overview", "recap",
    "motivation", "questions", "discussion", "lecture", "exercise", "exercises", "break", "literature",
    "einleitung", "zusammenfassung", "fazit", "ausblick", "übung", "pause",
}

PAGE_RE = re.compile(r"\b(?:Page|Seite|Slide|Folie)\s+(\d{1,4})\s+(?:of|von|/)\s+(\d{1,4})\b", re.I)


def tess_lang(language):
    wanted = TESS_LANG.get(language, "eng")
    try:
        have = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    except FileNotFoundError:
        return "eng"
    if wanted != "eng" and wanted in have:
        return f"{wanted}+eng"
    return "eng"


def ocr_images(images, language, workers=8, progress=None):
    """{name: analysis} for a list of image paths, in parallel."""
    if not shutil.which("tesseract"):
        raise AfterclassError("tesseract not found. Install it with: brew install tesseract")
    lang = tess_lang(language)

    def one(path):
        r = subprocess.run(["tesseract", str(path), "-", "-l", lang, "tsv"], capture_output=True, text=True)
        return path, r.stdout

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (path, tsv) in enumerate(pool.map(one, images), 1):
            out[path.stem] = analyze(parse_tsv(tsv))
            if progress:
                progress(i, len(images))
    return out


def parse_tsv(tsv):
    """Words [{text, left, top, w, h, conf, line}] and the page size from tesseract TSV output."""
    words, W, H = [], 0, 0
    for row in tsv.splitlines()[1:]:
        c = row.split("\t")
        if len(c) < 12:
            continue
        level, left, top, w, h, conf, text = int(c[0]), int(c[6]), int(c[7]), int(c[8]), int(c[9]), float(c[10]), c[11]
        if level == 1:
            W, H = w, h
        if level == 5 and text.strip():
            words.append({"text": text.strip(), "left": left, "top": top, "w": w, "h": h, "conf": conf,
                          "line": (int(c[2]), int(c[3]), int(c[4]))})
    return {"W": W or 1920, "H": H or 1080, "words": words}


def _good(word):
    return word["conf"] >= 60 and sum(ch.isalpha() for ch in word["text"]) >= 2


def analyze(parsed):
    H, words = parsed["H"], parsed["words"]
    full = " ".join(w["text"] for w in words)

    top_strip = [w for w in words if w["top"] < 0.04 * H]
    menu_hits = {w["text"].lower().strip(".,") for w in top_strip} & MENU_WORDS
    demo = len(menu_hits) >= 2

    page = pages = None
    if m := PAGE_RE.search(full):
        page, pages = int(m.group(1)), int(m.group(2))

    # Group words into lines
    lines = {}
    for w in words:
        lines.setdefault(w["line"], []).append(w)
    line_list = []
    for ws in lines.values():
        good = [w for w in ws if _good(w)]
        line_list.append({
            "text": " ".join(w["text"] for w in ws if w["conf"] >= 50),
            "good": " ".join(w["text"] for w in good),
            "n_good": len(good),
            "top": min(w["top"] for w in ws),
            "h": median(w["h"] for w in ws),
        })
    line_list.sort(key=lambda l: l["top"])

    # Title: tallest good line in the upper 30%, ignoring the menu bar and the page header
    title = ""
    cands = [l for l in line_list if l["n_good"] and 0.04 * H <= l["top"] < 0.3 * H and not PAGE_RE.search(l["text"])]
    if cands and not demo:
        title = max(cands, key=lambda l: (l["h"], -l["top"]))["good"]

    good_lines = [l for l in line_list if l["n_good"]]
    n_good = sum(l["n_good"] for l in good_lines)
    divider_lines = []
    if not demo and page is None and 1 <= n_good <= 8:
        centred = all(0.2 * H <= l["top"] <= 0.8 * H for l in good_lines)
        single_ok = n_good > 1 or good_lines[0]["good"].lower().strip(".:") in DIVIDER_WORDS
        if centred and single_ok:
            divider_lines = [l["good"] for l in good_lines]

    text = "\n".join(l["text"] for l in line_list if l["text"] and l["top"] >= (0.04 * H if demo else 0))
    return {
        "text": text,
        "title": title,
        "page": page,
        "pages": pages,
        "demo": demo,
        "words": n_good,
        "divider": divider_lines,
    }
