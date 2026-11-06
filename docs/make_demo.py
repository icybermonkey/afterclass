"""Make a made-up lecture (about 12 minutes: slides PDF + narrated video) for the README screenshots.

    uv run python docs/make_demo.py <out-folder>
    afterclass <out-folder>/demo.mp4 <lesson-folder> --deck <out-folder>/slides.pdf --ai --copy

Needs macOS `say` and ffmpeg. Nothing in it comes from a real class, so the screenshots are safe to publish.
The AI may split this short lecture into chapters under 3 minutes, which AfterClass merges away; the screenshots
use the deck's four outline chapters (set in data/ai.json, then rebuilt).
"""

import subprocess
import sys
from pathlib import Path

import pymupdf

W, H = 1280, 720
LECTURER, STUDENT = "Samantha", "Daniel"
INK, BOX = (0.13, 0.15, 0.2), (0.92, 0.94, 0.98)
WHITE, SOFT = (1, 1, 1), (0.8, 0.85, 0.95)
# a different background colour per slide: text-only changes on white are too subtle for scene detection
BANDS = [(0.13, 0.2, 0.36), (0.88, 0.45, 0.15), (0.05, 0.55, 0.5), (0.5, 0.3, 0.75), (0.85, 0.65, 0.1),
         (0.16, 0.42, 0.85), (0.8, 0.2, 0.3)]  # navy, orange, teal, purple, gold, blue, red: neighbours always contrast

# Chapters of about 3.5 minutes each: AfterClass merges AI chapters shorter than 3 minutes (steps/ai.py)
# (chapter, [(slide title, bullets, diagram, [(voice, text), ...]), ...])
CHAPTERS = [
    ("Why hash tables", [
        ("How Hash Tables Work", ["Data Structures · Lecture 4"], None, [
            (LECTURER, "Welcome back, everyone. Today we look at hash tables, the structure behind dictionaries in "
                       "Python, objects in JavaScript, and maps in most other languages. You use them every day, often "
                       "without noticing. By the end of this lecture you should know why lookups in a hash table are so "
                       "fast, what a collision is and how we deal with it, and when a hash table has to grow."),
            (LECTURER, "As usual, the slides are online, and there is a short exercise sheet for Thursday. Please try "
                       "the first two questions before the tutorial, they only need what we cover in the first half."),
        ]),
        ("The idea: jump straight to the answer", ["A list makes you search: O(n)", "A hash table computes where to look: O(1)",
                                                   "key -> hash function -> bucket index"], "pipeline", [
            (LECTURER, "Let's start with the problem. Say we store the phone numbers of everyone in this room in a plain "
                       "list. To find one person, we walk through the list from the start and compare names until we "
                       "hit the right one. With ten people that is fine. With ten million it is not. The work grows "
                       "with the size of the list, which is what we call linear time, or big O of n."),
            (LECTURER, "A hash table skips the search. It feeds the key, here the name, into a hash function. The "
                       "function returns a number, and that number tells us exactly which bucket to look in. So instead "
                       "of asking where is apple, we compute where apple has to be, and go straight there."),
            (LECTURER, "The important part is that this computation takes the same time no matter how many items we "
                       "store. On average, a lookup is constant time, big O of one."),
        ]),
        ("Where you meet them", ["Dictionaries, sets, caches", "Database indexes", "Counting words, removing duplicates"], None, [
            (LECTURER, "Before we look inside, where do you actually meet hash tables? Every dictionary and every set in "
                       "Python is one. Caches, for example in your browser, use them to find a stored page by its "
                       "address. Databases use hash indexes to find rows quickly. And many small programs, like counting "
                       "how often each word appears in a text or removing duplicates from a list, are really just a hash "
                       "table with a loop around it."),
            (LECTURER, "So it is worth understanding what happens underneath, because it explains when they are fast, "
                       "and the rare cases when they are not."),
        ]),
        ("A quick experiment", ["One million names in a list and in a set", "List: about 10 ms per lookup",
                                "Set: well under a microsecond"], None, [
            (LECTURER, "Here is a small experiment you can try at home. Put one million random names into a Python list, "
                       "and the same names into a set. Then look up a name that is not there, a thousand times."),
            (LECTURER, "With the list, every lookup has to compare against all one million names, and on a normal laptop "
                       "that takes around ten milliseconds each. With the set, each lookup takes well under a "
                       "microsecond. That is more than ten thousand times faster, for exactly the same data."),
            (LECTURER, "And the difference grows with the data. Double the number of names, and the list gets twice as "
                       "slow, while the set stays just as fast. That one experiment is the whole reason this lecture "
                       "exists. Let's see how the set does it."),
        ]),
    ]),
    ("Hash functions", [
        ("What a hash function does", ["Turns any key into a number", "Same key = always the same number",
                                       "index = hash(key) mod number_of_buckets"], None, [
            (LECTURER, "So what is a hash function? It takes a key of any type, a string, a number, a tuple, and turns "
                       "it into an integer. The table then takes that integer modulo the number of buckets, which gives "
                       "an index between zero and the number of buckets minus one."),
            (LECTURER, "The first rule is that a hash function must be deterministic. If apple gives forty two today, it "
                       "must give forty two tomorrow, and every time in between. Otherwise we would store apple in one "
                       "bucket and later look for it in another, and never find it again."),
            (LECTURER, "This is also why, in Python, you cannot use a list as a dictionary key. A list can change after "
                       "you insert it, and then its hash would change too. Tuples and strings never change, so they are "
                       "fine as keys."),
            (LECTURER, "One more detail: the hash function does not need to be clever about the number of buckets. It "
                       "produces one big number, and the table decides how to fold it into its current size. That keeps "
                       "the two jobs separate, and it means the same hash function works for a table with eight buckets "
                       "and for one with eight million."),
        ]),
        ("Good and bad hash functions", ["Bad: first letter of the key", "Good: every character changes the result",
                                         "Goal: keys spread evenly over buckets"], None, [
            (LECTURER, "The second rule is that a good hash function spreads keys evenly. Imagine a hash function that "
                       "only looks at the first letter of a name. All the names starting with S end up in the same "
                       "bucket, while the bucket for X stays almost empty. Lookups for S names get slow."),
            (LECTURER, "A good function lets every character change the result, and small changes in the key produce "
                       "very different numbers. Cat and car should land far apart. Real languages use carefully designed "
                       "functions for this, and for strings they also add a random seed, so that an attacker cannot "
                       "choose keys that all collide on purpose."),
            (LECTURER, "There is also a speed trade off. A hash function that reads every byte of a long key takes "
                       "longer to compute. For most keys, like names or numbers, that cost is tiny compared to searching "
                       "a list, so it is always worth paying."),
            (LECTURER, "In practice you almost never write your own hash function. But you should know these two rules, "
                       "because they explain everything that follows."),
        ]),
        ("Hashing your own objects", ["hash() and __hash__", "Equal objects need equal hashes",
                                      "Only hash what never changes"], None, [
            (LECTURER, "Where you do meet hash functions directly is when you put your own objects into a set or use them "
                       "as dictionary keys. In Python, the built in hash function calls a method named dunder hash on "
                       "your object. For a point with x and y coordinates, a good choice is simply to hash the tuple of "
                       "x and y, and let Python do the hard work."),
            (LECTURER, "There is one rule you must never break: if two objects are equal, they must have the same hash. "
                       "Otherwise the table looks for the second point in a different bucket than the first, and two "
                       "equal points both end up in your set. The opposite is allowed. Two different objects may share a "
                       "hash. That is just a collision, and we know how to handle those."),
            (LECTURER, "And only hash the parts of an object that never change. If you change a field that went into the "
                       "hash after inserting the object, the table can no longer find it. The object is still in there, "
                       "but it is lost in the wrong bucket. This bug is very hard to find, so avoid it from the start."),
        ]),
    ]),
    ("Collisions", [
        ("Collisions are normal", ["More keys than buckets: some must share", "Birthday paradox: happens early",
                                   "Two strategies: chaining, open addressing"], None, [
            (LECTURER, "Even with a perfect hash function, two keys will sometimes land in the same bucket. That is "
                       "called a collision. If we have more keys than buckets, it is simply unavoidable. But it happens "
                       "much earlier than most people expect. Think of the birthday paradox: with only twenty three "
                       "people in a room, two of them probably share a birthday, even though there are three hundred "
                       "and sixty five days."),
            (LECTURER, "So collisions are not an error. They are part of normal operation, and every hash table needs "
                       "a strategy for them. There are two main ones, and we look at both."),
        ]),
        ("Chaining", ["Each bucket holds a short list", "Lookup: hash, then search only that list",
                      "Used by Java's HashMap"], "buckets", [
            (LECTURER, "The simplest strategy is chaining. Every bucket keeps a small list of all the keys that landed "
                       "there. On the slide, dog and fig both hashed to bucket two, so bucket two holds a list with both "
                       "of them. To look up fig, we compute its bucket, and then search only that short list."),
            (STUDENT, "Sorry, quick question. Doesn't that make the lookup slow again if one list gets really long?"),
            (LECTURER, "Good question, and yes, it would. If all keys ended up in one bucket, we would be back to "
                       "searching a plain list. That is why the hash function has to spread keys evenly, and why we "
                       "watch how full the table gets. We come back to that in a few minutes."),
        ]),
        ("Open addressing", ["No lists: every key gets its own slot", "Taken? Probe the next free slot",
                             "Used by Python's dict"], None, [
            (LECTURER, "The second strategy is open addressing. Here there are no lists at all. Every slot holds at most "
                       "one key. When we insert a key and its slot is already taken, we probe: we try the next slot, "
                       "and the next, until we find a free one. A lookup follows the same path until it finds the key, "
                       "or hits an empty slot, which means the key is not there."),
            (LECTURER, "Open addressing uses memory very efficiently and is friendly to the processor cache, which is "
                       "why Python's dictionary uses a variant of it. The price is that it gets slow quickly when the "
                       "table fills up, because the probe sequences get long."),
        ]),
        ("Chaining or open addressing?", ["Chaining: simple, copes with a full table", "Open addressing: compact, cache friendly",
                                          "Deleting needs care: tombstones"], None, [
            (LECTURER, "So which one is better? As so often, it depends. Chaining is simple to implement and degrades "
                       "gracefully. Even when the table is fuller than planned, lookups just get a little slower. Deleting "
                       "a key is easy too: you remove it from its short list."),
            (LECTURER, "Open addressing needs less memory, because there are no extra list nodes, and it reads memory in "
                       "a row, which modern processors love. But deleting is tricky. If you simply empty a slot, a later "
                       "lookup may stop there too early and miss a key further down the probe path. So instead you leave "
                       "a marker, a so called tombstone, that says something used to be here, keep looking."),
            (LECTURER, "For the exam, you should be able to explain both strategies, draw what the table looks like after "
                       "a few inserts, and say why deleting needs tombstones in open addressing."),
        ]),
    ]),
    ("Growing the table", [
        ("Load factor", ["load factor = items / buckets", "Low: fast, but wastes memory",
                         "Typical limit: about 0.75"], None, [
            (LECTURER, "Both strategies depend on one number: the load factor. It is the number of items divided by the "
                       "number of buckets. A load factor of one half means the table is half full."),
            (LECTURER, "A low load factor means short chains and short probe sequences, so lookups are fast, but we "
                       "waste memory on empty buckets. A high load factor saves memory, but collisions pile up. Most "
                       "implementations pick a limit around three quarters. Python's dictionary resizes at about two "
                       "thirds."),
        ]),
        ("Resizing", ["Above the limit: double the buckets", "Rehash every key into the new table",
                      "Rare, so inserts stay O(1) on average"], None, [
            (LECTURER, "So what happens when we cross the limit? The table allocates a new array, usually twice as large, "
                       "and moves every key over. And it cannot just copy them, because the bucket index depends on the "
                       "number of buckets. Every key has to be hashed again. This is called rehashing."),
            (LECTURER, "Rehashing touches every item, so a single resize is expensive. But because we double the size "
                       "each time, resizes get rarer and rarer. If you spread the cost over all the inserts, each insert "
                       "still costs constant time on average. This idea is called amortized analysis, and we will see it "
                       "again with dynamic arrays."),
        ]),
        ("Summary", ["Hashing turns a key into a bucket index", "Collisions are normal: chain or probe",
                     "Keep the load factor low by resizing", "Average lookup and insert: O(1)"], None, [
            (LECTURER, "Let's sum up. A hash function turns a key into a bucket index, and it must be deterministic and "
                       "spread keys evenly. Collisions are normal, and we handle them with chaining or with open "
                       "addressing. The load factor tells us how full the table is, and resizing keeps it low. "
                       "Together, that gives us constant time lookups and inserts on average."),
            (LECTURER, "Next week we use hash tables to build a cache with a size limit. Please have a look at the "
                       "exercise sheet before Thursday. See you then."),
        ]),
    ]),
]
SLIDES = [s for _, slides in CHAPTERS for s in slides]


def draw(page, title, bullets, diagram, band):
    if diagram is None and len(bullets) == 1:  # title slide
        page.draw_rect(pymupdf.Rect(0, 0, W, H), color=None, fill=band)
        page.insert_textbox(pymupdf.Rect(80, 250, W - 80, 360), title, fontsize=54, fontname="hebo", color=WHITE, align=1)
        page.insert_textbox(pymupdf.Rect(80, 380, W - 80, 430), bullets[0], fontsize=26, fontname="helv",
                            color=SOFT, align=1)
        return
    page.draw_rect(pymupdf.Rect(0, 0, W, H), color=None, fill=band)
    page.insert_text((80, 100), title, fontsize=40, fontname="hebo", color=WHITE)
    page.draw_line((80, 135), (W - 80, 135), color=SOFT, width=1.5)
    width = 620 if diagram else W - 160
    for i, b in enumerate(bullets):
        y = 240 + i * 70
        page.draw_circle((92, y - 9), 6, color=None, fill=SOFT)
        page.insert_textbox(pymupdf.Rect(115, y - 30, 115 + width, y + 30), b, fontsize=26, fontname="helv", color=WHITE)
    if diagram == "pipeline":
        for i, label in enumerate(["\"apple\"", "hash()", "bucket 3"]):
            r = pymupdf.Rect(800, 200 + i * 165, 1180, 280 + i * 165)
            page.draw_rect(r, color=None, fill=BOX)
            page.insert_textbox(r + (0, 24, 0, 0), label, fontsize=28, fontname="cour", color=INK, align=1)
            if i < 2:
                page.draw_line((990, 282 + i * 165), (990, 363 + i * 165), color=SOFT, width=3)
    elif diagram == "buckets":
        for i, chain in enumerate([["cat"], [], ["dog", "fig"], ["owl"]]):
            y = 200 + i * 125
            r = pymupdf.Rect(800, y, 870, y + 70)
            page.draw_rect(r, color=None, fill=BOX)
            page.insert_textbox(r + (0, 20, 0, 0), str(i), fontsize=24, fontname="cour", color=INK, align=1)
            for j, key in enumerate(chain):
                k = pymupdf.Rect(910 + j * 140, y + 8, 1020 + j * 140, y + 62)
                page.draw_line((k.x0 - 40, y + 35), (k.x0, y + 35), color=SOFT, width=2)
                page.draw_rect(k, color=None, fill=WHITE)
                page.insert_textbox(k + (0, 14, 0, 0), key, fontsize=22, fontname="cour", color=INK, align=1)


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def main(out):
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    doc = pymupdf.open()
    for n, (title, bullets, diagram, _) in enumerate(SLIDES):
        draw(doc.new_page(width=W, height=H), title, bullets, diagram, BANDS[n % len(BANDS)])
    toc, page = [], 1  # an outline, like a real deck: AfterClass takes the chapters from it
    for chapter, slides in CHAPTERS:
        toc.append([1, chapter, page])
        page += len(slides)
    doc.set_toc(toc)
    doc.save(out / "slides.pdf")

    clips = []
    for n, (page, (*_, lines)) in enumerate(zip(doc, SLIDES)):
        png = work / f"slide{n}.png"
        page.get_pixmap(dpi=72).save(png)
        parts = []
        for m, (voice, text) in enumerate(lines):
            aiff = work / f"s{n}_{m}.aiff"
            run("say", "-v", voice, "-r", "140", "-o", str(aiff), text)
            parts.append(aiff)
        # a little slower than `say` speaks, with a short pause between speakers and slides
        inputs = [a for p in parts for a in ("-i", str(p))]
        chain = "".join(f"[{i}:a]aresample=44100,atempo=0.9,apad=pad_dur=0.8[a{i}];" for i in range(len(parts)))
        chain += "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]"
        wav = work / f"s{n}.wav"
        run("ffmpeg", "-y", *inputs, "-filter_complex", chain, "-map", "[out]", str(wav))
        clip = work / f"s{n}.mp4"
        run("ffmpeg", "-y", "-loop", "1", "-framerate", "25", "-i", str(png), "-i", str(wav), "-shortest",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(clip))
        clips.append(clip)
    listing = work / "clips.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips))
    run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out / "demo.mp4"))
    print(out / "demo.mp4")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo"))
