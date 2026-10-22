"""Slide deck (PDF, or PPTX via LibreOffice): page text, titles, dividers, renders, scene matching."""

import re
import shutil
import subprocess
from pathlib import Path
from statistics import median

from ..ui import LecapError
from .ocr import DIVIDER_WORDS

SOFFICE_APP = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
CONVERTIBLE = {".pptx", ".ppt", ".odp"}


def to_pdf(deck, data_dir):
    """Return a PDF path for the deck, converting PPTX with LibreOffice when needed."""
    ext = deck.suffix.lower()
    if ext == ".pdf":
        return deck
    if ext == ".key":
        raise LecapError("Keynote files can't be read directly. Export it as PDF (File → Export To → PDF).")
    if ext not in CONVERTIBLE:
        raise LecapError(f"unsupported deck format {ext} (use .pdf or .pptx)")
    soffice = shutil.which("soffice") or (SOFFICE_APP if Path(SOFFICE_APP).exists() else None)
    if not soffice:
        raise LecapError("converting PowerPoint needs LibreOffice (brew install --cask libreoffice), "
                         "or export the deck as PDF yourself")
    out = data_dir / (deck.stem + ".pdf")
    r = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(data_dir), str(deck)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not out.exists():
        raise LecapError(f"LibreOffice could not convert {deck.name}: {r.stderr.strip()[-300:]}")
    return out


def read(pdf):
    """Pages [{page, title, text, divider}] and the outline [(level, title, page)]."""
    import pymupdf

    try:
        doc = pymupdf.open(pdf)
    except Exception as e:
        raise LecapError(f"can't read the slides {Path(pdf).name} ({str(e).rstrip(".")}). Is it a valid PDF/PPTX?")
    raw = []
    sizes = []
    for i, page in enumerate(doc, 1):
        H = page.rect.height
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in line["spans"]).strip()
                size = max(s["size"] for s in spans)
                lines.append({"text": text, "size": round(size, 1), "y": line["bbox"][1] / H})
                for s in spans:  # weight each font size by its character count
                    sizes += [s["size"]] * len(s["text"].strip())
        lines.sort(key=lambda l: l["y"])
        raw.append((i, lines, page.get_text().strip()))
    body = median(sizes) if sizes else 12

    pages = []
    for i, lines, text in raw:
        words = len(text.split())
        top = [l for l in lines if l["y"] < 0.35]
        title = ""
        if top:
            biggest = max(l["size"] for l in top)
            first = next(k for k, l in enumerate(lines) if l["y"] < 0.35 and l["size"] >= biggest - 0.5)
            parts = [lines[first]["text"]]
            # A title wrapped over two lines keeps the same size and sits right below,
            # but a numbered or bulleted line ("1.", "•") under it is content, not title
            nxt = lines[first + 1] if first + 1 < len(lines) else None
            if (nxt and abs(nxt["size"] - lines[first]["size"]) < 0.5 and nxt["y"] - lines[first]["y"] < 0.1
                    and nxt["text"][:1].isalpha() and len(parts[0]) + len(nxt["text"]) <= 60):
                parts.append(nxt["text"])
            title = " ".join(parts) if _wordy(" ".join(parts)) else ""
        divider = []
        if lines and words <= 8:
            # Divider text is clearly larger than slide titles (which are ~1.2x body)
            big = [l["text"] for l in lines if l["size"] >= 1.35 * body]
            big_words = {w.lower() for w in " ".join(big).split() if sum(c.isalpha() for c in w) >= 2}
            if big and (len(big_words) >= 2 or big_words & DIVIDER_WORDS):
                divider = [l["text"] for l in lines if _wordy(l["text"])]
        pages.append({"page": i, "title": title, "text": text, "divider": divider})
    outline = [(lvl, t.strip(), p) for lvl, t, p in doc.get_toc() if t.strip()]
    return pages, outline


def render(pdf, out_dir, width=1600):
    """Render every page to out_dir/NNN.jpg."""
    import pymupdf

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    doc = pymupdf.open(pdf)
    for i, page in enumerate(doc, 1):
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        pix.save(str(out_dir / f"{i:03d}.jpg"), jpg_quality=85)
    return doc.page_count


def _wordy(text):
    return sum(c.isalpha() for c in text) >= 3


_TOKEN = re.compile(r"[a-zäöüß0-9][a-zäöüß0-9_\-]{2,}")


def tokens(text):
    return set(_TOKEN.findall(text.lower()))


def dice(a, b):
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def match(cards_ocr, pages):
    """Match video frames to deck pages.

    cards_ocr: [(card_id, ocr_dict)] in time order. Returns {card_id: (page, score, how)}.
    """
    n_pages = len(pages)
    page_tokens = [tokens(p["text"]) for p in pages]
    result, prev = {}, 0
    for cid, ocr in cards_ocr:
        if ocr.get("page") and ocr.get("pages") == n_pages:
            result[cid] = (ocr["page"], 1.0, "page number")
            prev = ocr["page"]
            continue
        toks = tokens(ocr.get("text", ""))
        if len(toks) < 2:
            continue
        best, best_score = None, 0.0
        for i, pt in enumerate(page_tokens, 1):
            score = dice(toks, pt)
            if prev and prev <= i <= prev + 4:
                score += 0.08  # lectures mostly move forward
            if score > best_score:
                best, best_score = i, score
        need = 0.6 if ocr.get("demo") else 0.45
        if best and best_score >= need:
            result[cid] = (best, round(min(best_score, 1.0), 2), "text")
            prev = best
    return result
