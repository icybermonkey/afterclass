# lecap

**Lecture recording in, searchable study page out.**

```
lecap lesson.mp4 lesson-01/ --deck slides.pdf --ai
```

creates

```
lesson-01/
  index.html      ← open this (works offline, straight from the folder)
  media/          the video (and the deck PDF)
  slides/         one frame per slide change
  deck/           sharp renders of the deck pages
  data/           transcript.srt, scenes.csv, ocr.json, deck.json, ai.json, lesson.json
```

## What the page does

- **Video + cards**: one card per slide/screen with what was said while it was shown. Click any sentence to jump there, and the current sentence is highlighted while playing.
- **Search** across the transcript, the slide text and your notes, with highlights, next/previous hit (`Enter`), and filters for slides, live demos or starred cards.
- **Chapters** in a sidebar, taken from the section slides (in the deck or in the video), or from the AI step.
- **Slide deck** (`--deck`): video frames are matched to deck pages, so cards show the sharp slide and its exact text, plus a link to that PDF page.
- **Notes and stars** per card, saved in your browser, with an **export to Markdown**.
- **Breaks and silence** are folded away, and live demos (screen recordings of apps) are marked.
- **Deep links** like `index.html#t=01:23:45` or `#q=opaque`, copied from the 🔗 button.
- **AI extras** (`--ai`): chapter summaries, clickable tags, a glossary and exam questions.
- Keyboard: `/` search · `j`/`k` next/previous card · `Enter` play card · `Space` play/pause · `s` star · `n` note.

## Install (macOS, Apple Silicon)

```bash
brew install ffmpeg tesseract uv
git clone https://github.com/icybermonkey/lecap.git ~/Projects/lecap
uv tool install --python 3.14 --editable "$HOME/Projects/lecap[claude]"
lecap --help
```

`--editable` means changes in the repo apply immediately. Leave out `[claude]` if you don't want the Claude API provider.
For German OCR: `brew install tesseract-lang`. To convert `.pptx` decks you need LibreOffice (`brew install --cask libreoffice`), or export the deck to PDF yourself.

## Usage

```bash
lecap <video> <output-folder> [options]

  --deck FILE        slide deck (.pdf/.pptx): sharp slides, exact text, chapters
  --title TEXT       page title (default: file name)
  -l LANG            spoken language (default en)
  -t NUM             slide-change sensitivity, higher = fewer cards (default 4)
  -w                 word timestamps + extra hallucination filter (slower)
  --ai [PROVIDER]    AI extras: claude (API key) or claude-code (your Claude subscription)
  --ai-model MODEL   e.g. claude-sonnet-5-5, or sonnet for claude-code
  -y                 don't ask before paid API calls
  --redo STEP        redo one step: transcribe, scenes, ocr, deck, ai (repeatable)
  -f                 redo everything
  --link | --move    symlink or move the video instead of copying it
  --open             open the page when done
```

Steps are cached in `data/`, so a second run only redoes what you ask for, e.g. `lecap lesson.mp4 out/ -t 6 --redo scenes` for fewer cards.

Timing for a 2.5 h lecture on an M-series Mac: transcript ~5 min, slides ~3 min, OCR ~20 s.

## AI providers and keys

| Provider | Needs | Cost |
|---|---|---|
| `claude-code` | the [Claude Code](https://claude.com/claude-code) CLI, logged in | included in your Claude subscription (uses its limits) |
| `claude` | an API key from console.anthropic.com | pay per use, ≈ $0.30–0.40 per 2.5 h lecture (Claude Opus 5.5) |

`--ai` without a name picks `claude` when a key is set, otherwise `claude-code`. Before a paid call, lecap shows the token count and an estimated price and asks first.

Only text is sent (transcript and slide text), never the video or images.

**Keys are never stored in files.** Store them in the macOS Keychain:

```bash
lecap auth set claude      # asks for the key (hidden input)
lecap auth status          # shows which providers work (keys masked)
lecap auth remove claude
```

An `ANTHROPIC_API_KEY` environment variable or an `ant auth login` profile also works.

Non-secret defaults live in `~/.config/lecap/config.toml`:

```bash
lecap config show
lecap config set language de
lecap config set ai_provider claude-code
```

## How it works

1. **ffprobe** checks the input, **ffmpeg silencedetect** finds breaks.
2. **mlx-whisper** (large-v3-turbo) transcribes on the GPU. `condition_on_previous_text` is off and a filter drops typical Whisper hallucinations ("Thank you." in silence, repetition loops). lecap warns if a lot was dropped.
3. **PySceneDetect** finds slide changes, and **tesseract** reads each frame: title, "Page X of N", section slides, and macOS menu bars (= live demo).
4. With `--deck`, **PyMuPDF** reads the deck: page text, titles, divider pages, outline. Frames are matched to pages by page number or text similarity.
5. Cards, chapters and the transcript are combined into `data/lesson.json` and embedded into `index.html`.

## Project layout

```
src/lecap/
  cli.py            commands, options, auth/config
  model.py          source-neutral Lesson model (the page only reads this)
  sources/          input types: video (now); audio, pdf, pptx are registered as planned
  steps/            probe, transcribe, silence, scenes, ocr, deck, analyze, ai, render
  ai/               provider interface + claude (API) + claude_code (subscription)
  web/template.html the page (inline CSS/JS, no external requests)
```

**Adding an input type**: write `sources/<type>.py` that builds a `Lesson`, then register it in `sources/registry.py`.
**Adding an AI provider** (OpenAI, Gemini, Ollama, …): write `ai/<name>.py` implementing `ai/base.py:Provider`, then list it in `ai/registry.py`. All providers share the prompt and the result schema (`ai/schema.py`).

## Rename the tool

The command name is in two places: `[project.scripts]` in `pyproject.toml` and `APP_NAME` in `src/lecap/__init__.py`. To also rename the package, `git mv src/lecap src/<new>` and update the imports and `packages` in `pyproject.toml`. Then run `uv tool install --editable . --reinstall`.

## Development

```bash
uv sync
uv run pytest
uv run lecap clip.mp4 /tmp/out --deck slides.pdf
```

The tests need no network or key. The Claude API test intercepts the HTTP request.

## Notes

- mlx-whisper runs on Apple Silicon only. Other platforms would need a different transcriber (e.g. faster-whisper) behind `steps/transcribe.py`.
- PyMuPDF is AGPL-licensed. Keep that in mind when choosing this project's license.
