"""Ask the lesson: offline search (BM25) over the transcript, slides and glossary, plus an optional
LLM answer that cites moments of the video. Questions beyond the lesson go online only on request."""

import math
import re
from collections import Counter

from .ai.schema import AskAnswer, json_schema
from .ui import LecapError, fmt_time

STOP = set("""a an and are as at be but by can do does for from how i in is it its me my of on or so that the
their them then there these they this to up us was we what when where which who why will with you your
der die das und ist ein eine einer zu den dem des im in mit von für auf nicht sich wir ich es wie was wo
wann warum
start started starting begin begins talk talks talking talked discuss discussed discussing explain explained
mention mentioned speak speaking lecture lesson part about please tell show find
anfangen beginnen sprechen reden erklären erklärt erkläre zeig zeige thema""".split())  # question phrasing
WORD = re.compile(r"[a-zA-Zäöüß0-9][a-zA-Zäöüß0-9\-_]*")

ASK_SYSTEM = """You help a student with one recorded lecture. Answer from the lecture passages you are given
(transcript with times, slide text, glossary). Explain clearly and briefly, in the language of the question.
Put the start times (seconds) of the passages you used into "times". If the passages do not answer the
question, say so in one sentence and set in_lesson to false; do not invent lecture content."""

GENERAL_SYSTEM = """You help a student with one recorded lecture. The lecture does not fully answer the
question, so answer from your general knowledge, clearly marked as going beyond the lecture. Relate it to the
lecture passages where that helps and put the times (seconds) of related passages into "times".
Set in_lesson to false."""

WEB_SYSTEM = GENERAL_SYSTEM + " You may search the web; name your sources briefly in the answer."


SUFFIXES = ("ing", "ed", "es", "s", "e")  # graded/grading/grade -> grad (same rule in the page's search)


def stem(w):
    for suf in SUFFIXES:
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def words(text):
    return [stem(w) for w in (m.lower() for m in WORD.findall(text or "")) if w not in STOP and len(w) > 1]


def passages(lesson):
    """Searchable pieces: transcript paragraphs, slide texts, glossary entries."""
    out = []
    for c in lesson.get("cards", []):
        if c.get("kind") == "break":
            continue
        para = None
        for s in c.get("segments", []):
            if para is None or s.get("para"):
                para = {"kind": "speech", "card": c["id"], "t": s["start"], "speaker": s.get("speaker", ""),
                        "text": s["text"]}
                out.append(para)
            else:
                para["text"] += " " + s["text"]
        slide = " ".join(x for x in (c.get("title"), c.get("text")) if x)
        if slide.strip():
            out.append({"kind": "slide", "card": c["id"], "t": c.get("start") or 0, "text": slide[:800]})
    starts = {c["id"]: c.get("start") or 0 for c in lesson.get("cards", [])}
    for g in (lesson.get("ai") or {}).get("glossary", []):
        out.append({"kind": "glossary", "card": g.get("card", 0), "t": starts.get(g.get("card"), 0),
                    "text": f"{g['term']}: {g['definition']}"})
    return out


class Index:
    """Plain BM25, no dependencies."""

    def __init__(self, items, k1=1.4, b=0.75):
        self.items, self.k1, self.b = items, k1, b
        self.docs = [Counter(words(p["text"])) for p in items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def search(self, query, k=8):
        q = list(dict.fromkeys(words(query)))
        scored = []
        for i, d in enumerate(self.docs):
            s, matched = 0.0, 0
            for w in q:
                if w in d:
                    matched += 1
                    tf = d[w]
                    s += self.idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if s > 0:
                s *= (matched / len(q)) ** 2  # passages with all the question's terms beat a lucky single word
                kind = self.items[i]["kind"]
                s *= 1.3 if kind == "slide" else 1.2 if kind == "glossary" else 1.0
                scored.append((s, i))
        scored.sort(reverse=True)
        return [{**self.items[i], "score": round(s, 2)} for s, i in scored[:k]]


ASKS_FOR_START = re.compile(r"\b(start|begin|first|introduc|anfang|beginn|zuerst|erstmal)", re.I)


def first_mention(index, query):
    """The moment to jump to: for "where do we start talking about X" the earliest strong hit,
    otherwise the best one. Works without any model."""
    hits = index.search(query, k=20)
    if not hits:
        return None
    if not ASKS_FOR_START.search(query):
        return hits[0]
    best = hits[0]["score"]
    strong = [h for h in hits if h["score"] >= 0.6 * best]
    return min(strong, key=lambda h: h["t"])


def context_for(lesson, index, question, t=None, k=8):
    hits = index.search(question, k=k)
    if t is not None:  # "about this part": the passages around the current moment
        near = [p for p in index.items if p["kind"] == "speech" and abs(p["t"] - t) <= 90]
        hits = near[:6] + [h for h in hits if h not in near]
    lines = []
    for p in sorted(hits[:k + 6], key=lambda p: p["t"]):
        who = {"A": "Lecturer"}.get(p.get("speaker"), f"Speaker {p.get('speaker')}" if p.get("speaker") else "")
        label = {"slide": "SLIDE", "glossary": "GLOSSARY"}.get(p["kind"], who or "SPEECH")
        lines.append(f"[{p['t']:.0f}s = {fmt_time(p['t'])} | {label}] {p['text']}")
    chapters = "\n".join(f"- {c['title']}" for c in lesson.get("chapters", []))
    return hits, f"Lecture: {lesson.get('title')}\nChapters:\n{chapters}\n\nPassages:\n" + "\n".join(lines)


def answer(lesson, question, provider=None, model="", t=None, mode="lesson"):
    """mode: lesson (only lecture material) | general (general knowledge) | web (may search the web)."""
    index = Index(passages(lesson))
    hits, ctx = context_for(lesson, index, question, t)
    found = [{"t": h["t"], "card": h["card"], "kind": h["kind"], "text": h["text"][:240]} for h in hits[:6]]
    first = first_mention(index, question)
    if provider is None:  # offline search only
        return {"answer": "", "times": [first["t"]] if first else [], "in_lesson": bool(hits),
                "passages": found, "provider": None}
    system = {"lesson": ASK_SYSTEM, "general": GENERAL_SYSTEM, "web": WEB_SYSTEM}[mode]
    prompt = f"{ctx}\n\nQuestion: {question}"
    if t is not None:
        prompt += f"\n(The student is watching the moment at {fmt_time(t)}.)"
    kwargs = {"web": True} if mode == "web" else {}
    data, usage = provider.run(system, prompt, json_schema(AskAnswer), model, **kwargs)
    try:
        result = AskAnswer.model_validate(data)
    except Exception:
        raise LecapError("the model's answer did not have the expected format")
    times = sorted({round(x, 1) for x in result.times if 0 <= x <= (lesson.get("duration") or 1e9)})
    return {"answer": result.answer, "times": times[:6], "in_lesson": result.in_lesson and mode == "lesson",
            "passages": found, "provider": provider.name, "model": usage.model or model}
