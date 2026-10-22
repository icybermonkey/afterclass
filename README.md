# lecap

**Lecture recording in, searchable study page out. Offline first.**

```bash
lecap lesson.mp4 lesson-01/ --deck slides.pdf --ai   # build the page
lecap open lesson-01/                                # study: notes saved in the folder, Ask, settings
```

One self-contained folder per lesson, so it syncs well to a cloud drive or file share:

```
lesson-01/
  index.html      ← the page (also works straight from the folder, without `lecap open`)
  media/          the video (copied by default) and the deck PDF
  slides/         one frame per slide change
  deck/           sharp renders of the deck pages
  data/           transcript, scenes, OCR, speakers, AI results, lesson.json (generated, can be rebuilt)
  notes/          your stars, notes, speaker names and Ask history (never touched by a rebuild)
```

## The page

```
┌ search · Slides · Demos · ★ · Auto-scroll · Export · Theme · ⚙ ──────────────────────┐
│ chapters        │              video                │ Transcript │ Overview          │
│ [slide picture] │                                   │ [pic] 59:49 Opaque Predicates │
│  · subchapters  ├───────────────────────────────────┤  An opaque predicate is …     │
│ [slide picture] │ Ask: "Where do we talk about VMs?"│  ✎ your note                  │
│                 │ → answer · ▶ 1:49:09 · sources    │                               │
└─────────────────┴───────────────────────────────────┴───────────────────────────────┘
```

- **Left: chapters.** One slide picture per chapter. Below it are the subchapters (slide titles, long live demos) as plain text. Click to jump.
- **Middle: the video**, with an **Ask box** below it. A model (local first) reads the relevant parts of the lesson and answers in its own words, with a ▶ button to the main moment. The other moments are listed as small "sources". Questions beyond the lesson can go to Claude online, but only after a click.
- **Right: the transcript**, one part per slide or screen with its picture and an AI quick title (with `--ai`). Click any sentence to jump there. Other speakers (questions from the audience) are marked. ☆ stars and ✎ notes go straight into the transcript.
  - **Paragraphs**: each part is split at speaker changes, pauses and every ~4 sentences, with a small time and speaker label per paragraph; long parts show 2 paragraphs and "show all".
  - **Blocks**: each part is one continuous text block (like v0.1), with speaker names inline.
- **Overview**: the lesson summary, all your stars and notes by chapter (export to Markdown), and the glossary.
- **Search** in the top bar across the transcript, slide text and notes. Auto-scroll keeps chapters and transcript on what is playing.
- **◧ ◨** in the top bar (or `[` `]`) hide the chapter or transcript lane for a bigger video; drag the edges between the lanes to resize them (double-click resets). **⋯** next to Ask: shortcuts to add a local model, a Claude key or Claude Code.

## What runs where

| Step | Where |
|---|---|
| Transcription (Whisper), speakers, slide detection, OCR, deck matching, chapters from slides | **on your Mac**, offline |
| The page, search, notes | **on your Mac**, offline |
| `--ai` and Ask with a **local model** (LM Studio / Ollama) | **on your Mac**, offline |
| `--ai` and Ask with **Claude** (`claude-code` = your subscription, `claude` = API key) | online: only text is sent (transcript + slide text), never the video |

`--ai` and Ask use the local model first when one is running. Ask only goes online after you click (and asks first if that's set in the settings).

## What `--ai` adds

Without AI you get the transcript, slides, search and notes, plus chapters *if the slides have section pages*.
`--ai` (a local model or Claude) adds **chapter names for every lesson**, a **quick title for every part**, a **summary** and a **glossary**.
Ask always needs a model: local (offline, `lecap setup ai`) or Claude (online, only text is sent).

**Ask without `lecap open`** (when you just double-click `index.html`): the page talks to LM Studio or Ollama directly
if they allow browser pages. Allow it once with `lecap setup ai`, or yourself:
`lms server stop && lms server start --cors` (lets any website use your local model, not your files; to keep it after a
restart: LM Studio → Developer → Server Settings → Enable CORS) or, for Ollama,
`launchctl setenv OLLAMA_ORIGINS null && brew services restart ollama`. Notes then stay in that browser; with
`lecap open` they are saved in the lesson folder and Claude online is available too.

## Install (macOS, Apple Silicon)

```bash
brew install ffmpeg tesseract uv
git clone https://github.com/icybermonkey/lecap.git ~/Projects/lecap
uv tool install --python 3.14 --editable "$HOME/Projects/lecap[claude]"
lecap --help
```

`--editable` means code changes in the repo apply immediately. After a change to the dependencies, run the same command with `--reinstall`.
For German OCR: `brew install tesseract-lang`. `.pptx` decks need LibreOffice (`brew install --cask libreoffice`), or export them to PDF yourself.

## Local AI in 5 minutes

```bash
lecap setup ai
```

The wizard finds LM Studio or Ollama and recommends a model for your Mac's memory (32 GB → a ~14B model). It shows each command and **asks before every download or start**, then tests the model and saves the choice. It works the same from the ⚙ settings page. By hand with LM Studio:

```bash
lms server start
lms get qwen/qwen3-14b                                   # ~9 GB, once
lms load qwen/qwen3-14b --context-length 32768 -y        # enough context for a whole lecture
```

If a model's context is too small for a lecture, lecap works in parts and merges the result.

## Usage

```bash
lecap <video> <output-folder> [options]

  --deck FILE           slide deck (.pdf/.pptx): sharp slides, exact text, chapters
  --title TEXT          page title (default: file name)
  -l LANG               spoken language (default en)
  -t NUM                slide-change sensitivity, higher = fewer cards (default 4)
  -w                    word timestamps + extra hallucination filter (slower)
  --speakers / --no-speakers   tell speakers apart (default on)
  --ai [PROVIDER]       chapter names + glossary: local, claude-code (subscription), claude (API key); default: local first
  --ai-model MODEL      e.g. claude-sonnet-5-5, sonnet for claude-code, or a local model name
  -y                    don't ask before paid API calls
  --redo STEP           redo one step: transcribe, speakers, scenes, ocr, deck, ai (repeatable)
  -f                    redo everything (your notes/ are kept)
  --copy | --link | --move   how the video gets into media/ (default: copy, see settings)
  --open                open the page with `lecap open` when done

lecap open <folder>     the page with notes saved in the folder, Ask and settings (local server, this Mac only)
lecap settings          global settings in the browser
lecap setup ai          guided local AI setup
lecap auth set|status|remove claude    Claude API key in the macOS Keychain
lecap config show|set|path             settings in the terminal
```

Steps are cached in `data/`, so a rerun only redoes what you ask for, e.g. `lecap lesson.mp4 out/ -t 2 --redo scenes` for more cards.

Timing for a 2.5 h lecture on an M2 Pro: transcript ~5 min, speakers ~25 s, slides ~3.5 min, OCR ~20 s.

## Settings, keys and notes

- **Settings** are global for all lessons, in `~/.config/lecap/config.toml`. They are edited with `lecap settings` (browser) or `lecap config`. They are kept outside the installed tool on purpose, so upgrades and reinstalls don't wipe them. `LECAP_CONFIG=/path/config.toml` moves them.
- **API keys** live in the macOS Keychain (`lecap auth set claude`, or the settings page). They are never written to a file, a lesson folder or a log. An `ANTHROPIC_API_KEY` environment variable or an `ant auth login` profile also works.
- **Notes**: with `lecap open`, they are saved in `<lesson>/notes/notes.json` (atomic writes, cloud-sync friendly) and appear in every window. Opened straight from the folder (`file://`), the page keeps notes in that browser and shows the folder's notes from the last build.

`lecap open` runs a small server on `127.0.0.1` only. It refuses other hosts and origins, and the API needs a header other websites can't send.

## AI providers

| Provider | Needs | Cost |
|---|---|---|
| `local` | LM Studio or Ollama running with a model (`lecap setup ai`) | free, offline |
| `claude-code` | the [Claude Code](https://claude.com/claude-code) CLI, logged in | included in your Claude subscription (uses its limits) |
| `claude` | an API key from console.anthropic.com | pay per use, ≈ $0.30–0.40 per 2.5 h lecture (Claude Opus 5.5) |

Lectures on security topics can occasionally trip a model's safety filter. lecap then reports it and never shows a placeholder answer. The `claude` API provider automatically falls back to another model.

## How it works

1. **ffprobe** checks the input. **ffmpeg silencedetect** finds breaks, and cards are cut around them so breaks fold away.
2. **mlx-whisper** (large-v3-turbo) transcribes on the GPU, with a filter for typical Whisper hallucinations.
3. **SpeechBrain ECAPA** voice fingerprints (on the Apple GPU) separate the lecturer from other voices.
4. **PySceneDetect** finds slide changes. **tesseract** reads each frame: titles, "Page X of N", section slides, and macOS menu bars (= live demo).
5. With `--deck`, **PyMuPDF** reads the deck, and frames are matched to pages by page number or text similarity.
6. Cards, paragraphs, chapters (picture + subchapters) and speakers go into `data/lesson.json`, which is embedded in `index.html`.
7. Ask uses BM25 search over paragraphs, slides and the glossary, then a model that answers from those passages. For "where does X start" questions, the earliest strong match sets the ▶ moment.

## Project layout

```
src/lecap/
  cli.py            commands and options
  model.py          source-neutral Lesson model (the page only reads this)
  sources/          input types: video (now); audio, pdf, pptx are registered as planned
  steps/            probe, transcribe, silence, speakers, scenes, ocr, deck, analyze, ai, render
  ai/               provider interface + local, claude (API), claude_code (subscription)
  ask.py            offline search + answers for the Ask tab
  server.py         `lecap open` / `lecap settings`
  setup_ai.py       `lecap setup ai`
  web/              template.html (the page), settings.html
```

**Adding an input type**: write `sources/<type>.py` that builds a `Lesson`, then register it in `sources/registry.py`.
**Adding an AI provider** (OpenAI, Gemini, …): write `ai/<name>.py` implementing `ai/base.py:Provider`, then list it in `ai/registry.py`.

## Rename the tool

The command name is in two places: `[project.scripts]` in `pyproject.toml` and `APP_NAME` in `src/lecap/__init__.py`. To also rename the package, `git mv src/lecap src/<new>` and update the imports and `packages` in `pyproject.toml`. Then run `uv tool install --editable . --reinstall`.

## Development

```bash
uv sync
uv run pytest            # no network, no keys; AI providers are tested against fakes
```

If the repo lives in iCloud Drive, keep the virtualenv out of sync: `mkdir .venv.nosync && ln -s .venv.nosync .venv`. Otherwise iCloud hides files inside it and imports break.

## Notes

- mlx-whisper runs on Apple Silicon only. Other platforms would need a different transcriber (e.g. faster-whisper) behind `steps/transcribe.py`.
- PyMuPDF is AGPL-licensed. Keep that in mind when choosing this project's license.
- Generated lesson folders can contain webcam frames of classmates. Share the tool, not the lessons.
