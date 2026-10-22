"""Video lesson: transcript + slide frames (+ optional deck) → study page."""

import hashlib
import json
import shutil
import sys

from .. import ui
from ..model import Lesson
from ..steps import analyze, deck, media, ocr, probe, render, scenes, silence, transcribe
from ..ui import AfterclassError, fmt_time, human_size
from .base import Source


class VideoSource(Source):
    name = "video"
    extensions = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")

    def run(self, ctx):
        a, cfg = ctx.args, ctx.cfg
        language = a.language or cfg["language"]
        threshold = a.threshold or cfg["threshold"]
        model = a.model or cfg["whisper_model"]
        use_speakers = cfg["speakers"] if a.speakers is None else a.speakers
        total = 8 + bool(a.deck) + bool(a.ai) + bool(use_speakers)
        steps = ui.Steps(total)
        title = a.title or ctx.input.stem.replace("_", " ").replace("-", " ").strip()

        # 1. Probe
        steps.begin("Check the input", "Read duration and streams with ffprobe; afterclass needs a video track for slides.")
        info = probe.probe(ctx.input)
        if not info["has_video"]:
            raise AfterclassError("this file has no video track. Audio-only input is planned but not supported yet.")
        if not info["has_audio"]:
            ctx.warnings.append("the video has no audio track: the transcript will be empty")
        ui.kv("file", f"{ctx.input.name} ({human_size(ctx.input.stat().st_size)})")
        ui.kv("length", fmt_time(info["duration"]))
        ui.kv("video", f"{info['width']}×{info['height']}")
        steps.end()

        # The recording and slides are read where they are; they are only copied/moved/linked into
        # media/ at the very end, after every step succeeded (a failed run leaves your files untouched)
        dest = ctx.input
        mode = "copy" if a.copy else "link" if a.link else "move" if a.move else cfg["media"]

        # 3. Transcript
        raw_json = ctx.data / "transcript.raw.json"
        transcribed = False
        if ctx.cached("transcribe", raw_json):
            steps.skip("Transcribe speech", "transcript exists (use --redo transcribe to run Whisper again)")
            raw = json.loads(raw_json.read_text(encoding="utf-8"))
        else:
            steps.begin("Transcribe speech", f"Whisper ({model.split('/')[-1]}) on the GPU, language '{language}'. "
                        "This is the slow part: roughly 2 min per lecture hour.")
            if a.word_timestamps:
                ui.explain("Word timestamps + hallucination filter on (slower).")
            raw = transcribe.transcribe(dest, model, language, a.word_timestamps)
            transcribe.save(raw, raw_json, ctx.data / "transcript.srt")
            transcribed = True
            steps.end(f"{len(raw)} segments → data/transcript.srt")

        # 4. Silences
        sil_json = ctx.data / "silences.json"
        if ctx.cached("transcribe", sil_json):
            silences = json.loads(sil_json.read_text())
            steps.skip("Find silent parts", f"{len(silences)} silent stretches (cached)")
        else:
            steps.begin("Find silent parts", "ffmpeg silencedetect: breaks and silence before class get folded "
                        "on the page, and Whisper text inside them is dropped.")
            silences = silence.detect(dest, info["duration"])
            sil_json.write_text(json.dumps(silences))
            long = [s for s in silences if s[1] - s[0] >= 180]
            for s0, s1 in long:
                ui.info(f"{fmt_time(s0)}–{fmt_time(s1)}  ({(s1 - s0) / 60:.0f} min)")
            steps.end(f"{len(silences)} silent stretches, {len(long)} longer than 3 min")

        segments, report = transcribe.clean(raw, silences)
        speech_dropped = report["raw"] - report["kept"] - report["dropped"]["silence"] - report["dropped"]["empty"]
        if report["loops"] or speech_dropped > 0.2 * max(report["raw"], 1):
            ctx.warnings.append(
                f"{report['dropped_share']:.0%} of the transcript was filtered as Whisper hallucination "
                f"({report['loops']} repetition loops). If text is missing, try: --redo transcribe -w")

        # 5. Speakers (optional)
        speaker_list = []
        if use_speakers:
            speaker_list = self._speakers(ctx, steps, dest, segments, transcribed)

        # 5. Scenes
        csv_path = ctx.data / "scenes.csv"
        scenes_redone = False
        if ctx.cached("scenes", csv_path, ctx.slides, scenes.done_marker(ctx.data)):
            scene_list = scenes.parse_csv(csv_path)
            steps.skip("Detect slide changes", f"{len(scene_list)} scenes (cached, use --redo scenes or -t to change)")
        else:
            steps.begin("Detect slide changes", f"PySceneDetect (adaptive, threshold {threshold}): a new card starts "
                        "whenever the picture changes. Higher -t = fewer cards.")
            scenes.detect(dest, ctx.data, ctx.slides, threshold)
            scene_list = scenes.parse_csv(csv_path)
            scenes_redone = True
            steps.end(f"{len(scene_list)} scenes → slides/")

        # 6. OCR
        ocr_json = ctx.data / "ocr.json"
        if not scenes_redone and ctx.cached("ocr", ocr_json):
            ocr_data = json.loads(ocr_json.read_text(encoding="utf-8"))
            steps.skip("Read slide text", f"{len(ocr_data)} frames (cached)")
        else:
            steps.begin("Read slide text", f"tesseract OCR ({ocr.tess_lang(language)}) on every frame: makes slide "
                        "text searchable and finds titles, page numbers, section dividers and live demos.")
            images = sorted(ctx.slides.glob("*.jpg"))
            ocr_data = ocr.ocr_images(images, language, progress=_progress)
            ocr_json.write_text(json.dumps(ocr_data, ensure_ascii=False), encoding="utf-8")
            n_demo = sum(o["demo"] for o in ocr_data.values())
            n_div = sum(bool(o["divider"]) for o in ocr_data.values())
            steps.end(f"{len(ocr_data)} frames: {n_demo} live demos, {n_div} section slides")

        # 7. Deck
        pages, outline, deck_rel = None, [], None
        if a.deck:
            pages, outline, deck_rel = self._deck(ctx, steps)

        # 8. Cards + chapters
        steps.begin("Build cards and chapters", "Merge repeated frames, attach speech to the slide it was said on, "
                    "fold breaks, and find chapters.")
        cards = analyze.build_cards(scene_list, ocr_data, ctx.slides)
        matched = {}
        if pages:
            matched = analyze.apply_deck(cards, pages)
            by_how = {}
            for _p, _s, how in matched.values():
                by_how[how] = by_how.get(how, 0) + 1
            ui.info(f"deck: {len(matched)} of {len(cards)} frames matched to a page "
                    f"({', '.join(f'{n} by {h}' for h, n in by_how.items())})")
        n_before = len(cards)
        cards = analyze.merge_repeats(cards)
        cards = analyze.split_at_silences(cards, silences)
        analyze.assign_segments(cards, segments)
        analyze.mark_breaks(cards, silences)
        analyze.mark_paragraphs(cards)
        if pages:
            chapters, source = analyze.chapters_from_deck(cards, pages, outline)
            if not chapters:
                chapters, source = analyze.chapters_from_video(cards)
        else:
            chapters, source = analyze.chapters_from_video(cards)
        chapters = analyze.with_start(chapters, cards)

        kinds = {}
        for c in cards:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        lesson = Lesson(
            id=analyze.lesson_id(ctx.input.name, info["duration"]),
            title=title, source="video", duration=info["duration"],
            media=f"media/{dest.name}", deck=deck_rel, deck_pages=len(pages) if pages else None,
            language=language, cards=cards, chapters=chapters, silences=silences,
            speakers=speaker_list, transcript_view=cfg["transcript_view"],
            stats={"transcript": report, "kinds": kinds, "chapter_source": source,
                   "frames": n_before, "deck_matched": len(matched)},
        )
        ui.info(f"{n_before} frames → {len(cards)} cards (" + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")")
        ui.info(f"transcript: {report['kept']} of {report['raw']} segments kept "
                f"(dropped: {', '.join(f'{n} {k}' for k, n in report['dropped'].items() if n) or 'none'})")
        ui.info(f"{len(chapters)} chapters from {source}:")
        for ch in chapters:
            ui.info(f"  {fmt_time(cards[ch.card].start)}  {ch.title}")
        if pages:
            shown = {c.deck_page for c in cards if c.deck_page}
            unseen = [p["page"] for p in pages if p["page"] not in shown and p["text"].strip()]
            if unseen:
                ui.info(f"deck pages never shown in the recording: {_ranges(unseen)}")
        steps.end()

        # 9. AI (a failure here never costs you the page)
        if a.ai:
            try:
                self._ai(ctx, steps, lesson)
            except AfterclassError as e:
                ui.warn(str(e))
                ctx.warnings.append(f"AI step failed, page written without AI extras: {str(e)[:200]}")
                steps.end()

        # Everything succeeded: now place the recording and the slides
        steps.begin("Place the files", f"Recording: {mode}, slides: {a.deck_mode or cfg['deck_media']} "
                    "(change in ⚙ Settings). Done only now, so a failed run leaves your files untouched.")
        placed, what = media.place(ctx.input, ctx.media, mode)
        ui.info(f"recording {what} → media/{placed.name}")
        if a.deck:
            self._place_deck(ctx)
        steps.end()

        # Render
        steps.begin("Write the page", "index.html with everything embedded; open it straight from the folder.")
        analyze.decorate_chapters(lesson.chapters, lesson.cards, {p["page"] for p in pages or [] if p["divider"]})
        lesson.save(ctx.data / "lesson.json")
        out = ctx.out / "index.html"
        size = render.render(lesson, out)
        steps.end(f"index.html ({human_size(size)})")
        return out, steps

    # --- speakers ------------------------------------------------------------

    def _speakers(self, ctx, steps, video, segments, transcribed):
        """Label every cleaned segment with a speaker (in place). Returns the speaker summary."""
        from ..steps import speakers

        sp_json = ctx.data / "speakers.json"
        starts = [s["start"] for s in segments]
        if not transcribed and ctx.cached("speakers", sp_json):
            cached = json.loads(sp_json.read_text())
            if cached.get("starts") == starts:
                for s, lab in zip(segments, cached["labels"]):
                    s["speaker"] = lab
                steps.skip("Tell speakers apart", ", ".join(f"{x['label']} {x['minutes']} min" for x in cached["summary"])
                           + " (cached)")
                return cached["summary"]
        steps.begin("Tell speakers apart", "Experimental, offline: a voice fingerprint per sentence (SpeechBrain "
                    "ECAPA). The main voice is the lecturer; other voices become Speaker B, C, …")
        try:
            audio = speakers.load_audio(video)
            ui.info("computing voice fingerprints…")
            emb = speakers.embed(audio, segments, progress=_progress_n("segments"))
        except AfterclassError as e:
            ctx.warnings.append(f"speaker detection skipped: {e}")
            ui.warn(str(e))
            steps.end()
            return []
        labels = speakers.assign(segments, emb)
        summary = speakers.summary(segments, labels)
        for s, lab in zip(segments, labels):
            s["speaker"] = lab
        sp_json.write_text(json.dumps({"starts": starts, "labels": labels, "summary": summary}))
        changes = sum(1 for x, y in zip(labels, labels[1:]) if x != y)
        steps.end(", ".join(f"{x['label']} {x['minutes']} min" for x in summary) + f"; {changes} speaker changes")
        return summary

    # --- deck ----------------------------------------------------------------

    def _deck(self, ctx, steps):
        src = ctx.args.deck
        deck_json = ctx.data / "deck.json"
        if ctx.cached("deck", deck_json, ctx.deck):
            d = json.loads(deck_json.read_text(encoding="utf-8"))
            if d.get("source") == src.name:
                steps.skip("Read the slide deck", f"{len(d['pages'])} pages (cached)")
                return d["pages"], [tuple(o) for o in d["outline"]], d["rel"]
        steps.begin("Read the slide deck", "Exact slide text, titles and section pages from the deck; each video "
                    "frame is matched to its page so cards show the sharp slide.")
        pdf = deck.to_pdf(src, ctx.data)  # a PPTX becomes data/<name>.pdf; a PDF is read where it is
        if pdf != src:
            ui.info(f"converted {src.name} → PDF with LibreOffice")
        pages, outline = deck.read(pdf)
        n = deck.render(pdf, ctx.deck)
        rel = f"media/{pdf.name}"  # where the PDF will be once the run succeeded
        deck_json.write_text(json.dumps({"source": src.name, "pages": pages, "outline": outline, "rel": rel},
                                        ensure_ascii=False),
                             encoding="utf-8")
        divs = [p for p in pages if p["divider"]]
        ui.info(f"{n} pages, {len(divs)} section pages, outline entries: {len(outline)}")
        steps.end(f"pages rendered → deck/, PDF → {rel}")
        return pages, outline, rel

    def _place_deck(self, ctx):
        """Put the slides into media/ (their own copy/link/move setting); a converted PDF moves next to them."""
        src = ctx.args.deck
        placed, what = media.place(src, ctx.media, ctx.args.deck_mode or ctx.cfg["deck_media"])
        ui.info(f"slides {what} → media/{placed.name}")
        converted = ctx.data / (src.stem + ".pdf")
        if src.suffix.lower() != ".pdf" and converted.exists():
            shutil.move(converted, ctx.media / converted.name)
            ui.info(f"converted PDF → media/{converted.name}")

    # --- ai ------------------------------------------------------------------

    def _ai(self, ctx, steps, lesson):
        from ..ai import registry
        from ..steps import ai as ai_step

        a, cfg = ctx.args, ctx.cfg
        provider = registry.pick(a.ai if a.ai != "default" else cfg["ai_provider"])
        model = a.ai_model or cfg["ai_model"] or provider.default_model
        where = ("offline, on this Mac" if not registry.is_online(provider)
                 else "online: only text is sent (transcript + slide text), no video or images")
        steps.begin("AI study extras", f"{provider.label} ({model or 'default model'}), {where}. "
                    "Chapter names and a glossary.")
        system, prompt = ai_step.build_prompt(lesson)
        # Content fingerprint without card numbers: re-cut cards don't invalidate the AI result
        content = "\n".join(s.text for c in lesson.cards for s in c.segments) + "".join(c.text for c in lesson.cards)
        digest = hashlib.sha256(content.encode()).hexdigest()[:16]
        ai_json = ctx.data / "ai.json"
        if ctx.cached("ai", ai_json):
            cached = json.loads(ai_json.read_text(encoding="utf-8"))
            if cached.get("input") != digest or "card_times" not in cached:
                ctx.warnings.append("the transcript or slides changed since the AI step ran; use --redo ai to refresh it")
            try:
                result = ai_step.parse(cached["result"])
            except AfterclassError:
                ai_json.unlink()  # never keep an unusable answer around
                raise
            ai_step.apply(lesson, result, cached["meta"], cached.get("card_times"))
            steps.end(f"reused data/ai.json ({cached['meta']['provider']}, {cached['meta']['model']})")
            return
        ok, why = provider.available()
        if not ok:
            ctx.warnings.append(f"AI step skipped: {why}")
            ui.warn(f"skipped: {why}")
            steps.end()
            return
        ui.kv("input", f"~{len(prompt) // 4:,} tokens of text")
        est = provider.estimate(system, prompt, model)
        if est and est.cost_usd is not None:
            ui.kv("estimate", f"{est.input_tokens:,} input tokens → about ${est.cost_usd:.2f} (API, pay per use)")
            if not a.yes:
                if not sys.stdin.isatty():
                    ctx.warnings.append("AI step skipped: costs money; pass --yes to allow it without a prompt")
                    steps.end()
                    return
                if input(f"{ui.INDENT}Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                    ui.info("skipped by you")
                    steps.end()
                    return
        elif provider.name == "claude-code":
            ui.explain("Runs through your Claude subscription (no extra cost, counts against its usage limits).")
        elif provider.name == "local":
            model = provider.pick_model(model)
            ui.kv("model", provider.describe(model))
        ui.info("waiting for the model (online usually 1–4 minutes; local models can take longer)…")
        result, usage = ai_step.run(provider, lesson, model)
        meta = {"provider": provider.name, "model": usage.model or model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": usage.cost_usd}
        ai_json.write_text(json.dumps({"input": digest, "meta": meta, "card_times": ai_step.card_times(lesson),
                                       "result": result.model_dump()}, ensure_ascii=False, indent=1), encoding="utf-8")
        ai_step.apply(lesson, result, meta)
        cost = f", ${usage.cost_usd:.2f}" if usage.cost_usd is not None else ""
        steps.end(f"{len(result.chapters)} chapters, {len(result.glossary)} glossary terms "
                  f"({usage.input_tokens:,} in / {usage.output_tokens:,} out{cost})")


def _progress_n(unit):
    def progress(i, n):
        if sys.stdout.isatty():
            sys.stdout.write(f"\r{ui.INDENT}{ui.DIM}{i}/{n} {unit}{ui.RESET}\033[K")
            if i == n:
                sys.stdout.write("\n")
            sys.stdout.flush()
    return progress


_progress = _progress_n("frames")


def _ranges(nums):
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)
