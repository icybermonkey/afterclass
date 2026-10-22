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

ASK_SYSTEM = """You help a student with one recorded lecture, using the lecture passages you are given
(transcript with times, slide text, glossary). Think about what the student wants, then answer in your own
words in 2-6 sentences, in the language of the question. Never answer with timestamps only:
- "Where/when is X discussed?": say when it starts and summarize what is said about X there.
- "What is X / explain X": explain it the way the lecture does, with its examples.
Put the start time (seconds) of the single most relevant passage into main_time and the times of other
passages you used into sources. If the passages do not answer the question, say so in one sentence,
set in_lesson to false and main_time to -1; never invent lecture content."""

GENERAL_SYSTEM = """You help a student with one recorded lecture. The lecture does not fully answer the
question, so answer from your general knowledge in 2-6 sentences, clearly marked as going beyond the lecture.
Relate it to the lecture passages where that helps: main_time = the most related passage (or -1),
sources = other related passages. Set in_lesson to false."""

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
    if t is not None:  # what is on screen right now, for questions like "explain this"
        near = [p for p in index.items if p["kind"] == "speech" and abs(p["t"] - t) <= 60]
        hits = hits + [p for p in near[:3] if p not in hits]
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
    first = first_mention(index, question)

    def source(t_):
        """A time plus the passage text that starts there (for the grey, expandable sources)."""
        near = min(index.items, key=lambda p: abs(p["t"] - t_), default=None)
        text = near["text"][:200] if near and abs(near["t"] - t_) <= 5 else ""
        return {"t": round(t_, 1), "text": text}

    if provider is None:  # no model: the best moment and the search hits, said honestly
        main = first["t"] if first else None
        return {"answer": "", "main_time": main, "in_lesson": bool(hits), "provider": None,
                "sources": [source(h["t"]) for h in hits[:6] if h["t"] != main]}
    system = {"lesson": ASK_SYSTEM, "general": GENERAL_SYSTEM, "web": WEB_SYSTEM}[mode]
    prompt = f"{ctx}\n\nQuestion: {question}"
    if first and ASKS_FOR_START.search(question):
        prompt += f"\n(Search hint: the topic first appears at {first['t']:.0f}s = {fmt_time(first['t'])}.)"
    if t is not None:
        prompt += (f"\n(The student is watching {fmt_time(t)}. Use that only if the question says "
                   "'this', 'here' or 'now'.)")
    kwargs = {"web": True} if mode == "web" else {}
    data, usage = provider.run(system, prompt, json_schema(AskAnswer), model, **kwargs)
    try:
        result = AskAnswer.model_validate(data)
    except Exception:
        raise LecapError("the model's answer did not have the expected format")
    duration = lesson.get("duration") or 1e9
    main = result.main_time if 0 <= result.main_time <= duration else (first["t"] if first and result.in_lesson else None)
    extra = [] if main is None else [main]
    if main is not None and first and ASKS_FOR_START.search(question) and first["t"] < main - 30:
        main = first["t"]  # "where do we start…": the search knows the earliest strong match better
    times = sorted({round(x, 1) for x in [*result.sources, *extra] if 0 <= x <= duration and abs(x - main) > 2}
                   if main is not None else {round(x, 1) for x in result.sources if 0 <= x <= duration})
    return {"answer": result.answer, "main_time": round(main, 1) if main is not None else None,
            "sources": [source(x) for x in times[:6]], "in_lesson": result.in_lesson and mode == "lesson",
            "provider": provider.name, "model": usage.model or model}
