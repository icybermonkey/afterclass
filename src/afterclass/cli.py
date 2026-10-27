"""Command line: `afterclass <video> <out-dir> [options]`, plus `afterclass auth …` and `afterclass config …`."""

import argparse
import getpass
import shutil
import signal
import sys
from pathlib import Path

from . import APP_NAME, DISPLAY_NAME, __version__, config, ui
from .ui import AfterclassError

REDO_STEPS = ("transcribe", "speakers", "scenes", "ocr", "deck", "ai")


def build_parser():
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Turn a lecture recording into a searchable study page: index.html + subfolders.",
        epilog=(f"Also: `{APP_NAME} app` (window to start a lesson), `{APP_NAME} install-app` ({DISPLAY_NAME}.app in /Applications), "
                f"`{APP_NAME} open <folder>`, `{APP_NAME} convert`, `{APP_NAME} settings`, `{APP_NAME} setup ai`, `{APP_NAME} auth --help`."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", type=Path, help="lecture video (.mp4, .mov, …)")
    p.add_argument("out", type=Path, help="output folder (gets index.html, media/, slides/, data/ …)")
    p.add_argument("--deck", type=Path, metavar="FILE", help="the slide deck (.pdf or .pptx) for sharp slides, exact text and chapters")
    p.add_argument("--title", help="page title (default: from the file name)")
    g = p.add_argument_group("transcript and slides")
    g.add_argument("-l", "--language", metavar="LANG", help="spoken language, e.g. en, de (default from config: en)")
    g.add_argument("-t", "--threshold", type=float, metavar="NUM", help="slide change sensitivity, higher = fewer cards (default 4)")
    g.add_argument("-m", "--model", metavar="MODEL", help="mlx-whisper model (default whisper-large-v3-turbo)")
    g.add_argument("-w", "--word-timestamps", action="store_true", help="word timestamps + hallucination filter (slower)")
    g.add_argument("--speakers", action=argparse.BooleanOptionalAction, default=None,
                   help="tell the lecturer and other speakers apart, offline (default from config: on)")
    g = p.add_argument_group("AI extras")
    g.add_argument("--ai", nargs="?", const="default", metavar="PROVIDER",
                   help="chapters, summaries, tags, glossary, exam questions. PROVIDER: claude (API key) or "
                        "claude-code (your subscription); default: claude with a key, else claude-code")
    g.add_argument("--ai-model", metavar="MODEL", help="model for the AI step (default: provider's best)")
    g.add_argument("-y", "--yes", action="store_true", help="don't ask before paid AI calls")
    g = p.add_argument_group("re-running")
    g.add_argument("-f", "--force", action="store_true", help="redo every step")
    g.add_argument("--redo", action="append", choices=REDO_STEPS, metavar="STEP",
                   help=f"redo one step, repeatable: {', '.join(REDO_STEPS)}")
    g = p.add_argument_group("media")
    m = g.add_mutually_exclusive_group()
    m.add_argument("--copy", action="store_true", help="copy the recording into media/ (default, see `afterclass config set media`)")
    m.add_argument("--link", action="store_true", help="symlink the video into media/ instead of copying")
    m.add_argument("--move", action="store_true", help="move the video into media/ instead of copying")
    g.add_argument("--deck-mode", choices=("copy", "link", "move"), help="what happens to the slides (default from settings: copy)")
    g.add_argument("--notify", action=argparse.BooleanOptionalAction, default=None,
                   help="macOS notification when the lesson is ready or failed (default from settings: on)")
    g.add_argument("--open", action="store_true", help="open the page with notes, Ask and settings (afterclass open) when done")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p


def _cancel(*_):
    raise KeyboardInterrupt


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # The app's Cancel button sends SIGTERM: treat it like Ctrl+C so cleanup runs
    signal.signal(signal.SIGTERM, _cancel)
    # Keep stdout and stderr in order when piped into a log
    sys.stdout.reconfigure(line_buffering=True)
    try:
        if argv and argv[0] == "auth":
            return auth_main(argv[1:])
        if argv and argv[0] == "config":
            return config_main(argv[1:])
        if argv and argv[0] == "app":
            from .gui import main as app_main
            return app_main(argv[1:])
        if argv and argv[0] == "convert":
            return convert_main(argv[1:])
        if argv and argv[0] == "install-app":
            from .macapp import main as install_main
            return install_main(argv[1:])
        if argv[:2] == ["setup", "ai"]:
            from .setup_ai import wizard
            return wizard()
        if argv and argv[0] in ("open", "settings", "open-url"):
            from .server import serve_main
            return serve_main(argv)
        return run(build_parser().parse_args(argv))
    except AfterclassError as e:
        ui.error(str(e))
        return 1
    except KeyboardInterrupt:
        ui.error("cancelled")
        return 130


def preflight(args):
    missing = []
    for tool, hint in (("ffmpeg", "brew install ffmpeg"), ("ffprobe", "brew install ffmpeg"),
                       ("tesseract", "brew install tesseract")):
        if not shutil.which(tool):
            missing.append(f"{tool} ({hint})")
    if missing:
        raise AfterclassError("missing tools: " + ", ".join(missing))
    if not args.input.exists():
        raise AfterclassError(f"input not found: {args.input}")
    if args.deck and not args.deck.exists():
        raise AfterclassError(f"deck not found: {args.deck}")


def reuse_moved(path, out):
    """With media=move the originals end up in <out>/media/: a rerun with the old path finds them there."""
    if path and not path.exists():
        moved = out.expanduser() / "media" / path.name
        if moved.exists():
            ui.info(f"using {moved} ({path.name} was moved into the lesson folder)")
            return moved
    return path


def run(args):
    from .sources.registry import pick

    args.input = reuse_moved(args.input, args.out)
    args.deck = reuse_moved(args.deck, args.out)
    preflight(args)
    args.input = args.input.expanduser().resolve()
    args.out = args.out.expanduser().resolve()
    if args.deck:
        args.deck = args.deck.expanduser().resolve()
    source = pick(args.input)
    cfg = config.load()

    from .sources.base import Ctx

    if args.out.exists() and any(args.out.iterdir()) and not (args.out / "data").exists():
        raise AfterclassError(f"{args.out} exists and is not a {APP_NAME} folder. Pick an empty or new folder.")
    # A folder this run creates is removed again if the run is cancelled; an existing lesson never is
    fresh = not args.out.exists() or not any(args.out.iterdir())
    ctx = Ctx(input=args.input, out=args.out, args=args, cfg=cfg)
    ui.title(f"{APP_NAME} {__version__}: lecture → study page")
    ui.kv("input", args.input)
    if args.deck:
        ui.kv("deck", args.deck)
    ui.kv("output", args.out)

    notify = cfg["notify"] if args.notify is None else args.notify
    try:
        index, steps = source.run(ctx)
    except KeyboardInterrupt:
        if fresh and args.out.exists():
            shutil.rmtree(args.out, ignore_errors=True)
            ui.info(f"cancelled: removed the unfinished folder {args.out} (your files were not touched)")
        else:
            ui.info("cancelled: your files were not touched")
        raise
    except AfterclassError as e:
        if notify:
            ui.notify(f"{DISPLAY_NAME}: lesson failed", f"{args.input.name}: {str(e)[:120]}", sound="Basso")
        raise
    if notify:
        ui.notify(f"{DISPLAY_NAME}: lesson ready", f"{args.title or args.input.stem} is ready to study ({steps.elapsed()})")

    print(f"\n{ui.GREEN}{ui.BOLD}Done{ui.RESET} in {steps.elapsed()}: {index}")
    for w in ctx.warnings:
        ui.warn(w)
    ui.info(f"open it with notes, settings and Ask: {APP_NAME} open '{index.parent}'")
    ui.info(f"or just the page: open '{index}'")
    if args.open:
        from .server import serve
        return serve(index.parent)
    return 0


# --- auth ------------------------------------------------------------------


def auth_main(argv):
    from .ai.registry import PROVIDERS

    keyed = [n for n, p in PROVIDERS.items() if p.needs_key]
    p = argparse.ArgumentParser(prog=f"{APP_NAME} auth",
                                description="Store AI API keys in the macOS Keychain (never in files).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="store a key (asked for hidden, not echoed)")
    s.add_argument("provider", choices=keyed)
    sub.add_parser("status", help="show which providers can be used")
    r = sub.add_parser("remove", help="delete a stored key")
    r.add_argument("provider", choices=keyed)
    a = p.parse_args(argv)

    if a.cmd == "set":
        key = getpass.getpass(f"Paste your {a.provider} API key (input hidden): ").strip()
        if not key:
            raise AfterclassError("no key entered")
        config.set_secret(a.provider, key)
        ui.ok(f"stored in the macOS Keychain ({APP_NAME}/{a.provider}): {config.mask(key)}")
    elif a.cmd == "remove":
        if config.delete_secret(a.provider):
            ui.ok(f"removed the {a.provider} key from the Keychain")
        else:
            ui.info(f"no {a.provider} key in the Keychain")
    else:
        for name, prov in PROVIDERS.items():
            ok, why = prov.available()
            if prov.needs_key:
                key, src = config.get_secret(name)
                detail = f"{config.mask(key)} from {src}" if key else ("via `ant` login" if ok else why)
            else:
                detail = "no key needed" if ok else why
            mark = f"{ui.GREEN}✔{ui.RESET}" if ok else f"{ui.YELLOW}✗{ui.RESET}"
            ui.info(f"{mark} {name:<12} {detail}")
    return 0


# --- config ----------------------------------------------------------------


def convert_main(argv):
    p = argparse.ArgumentParser(prog=f"{APP_NAME} convert",
                                description=f"Make lesson folders {DISPLAY_NAME} documents: <name>.afterclass, one icon in "
                                            f"Finder that opens in {DISPLAY_NAME} (run `{APP_NAME} install-app` once).")
    p.add_argument("folders", nargs="*", type=Path, help="lesson folders (default: the app's recent lessons)")
    a = p.parse_args(argv)
    from .gui import LESSON_EXT, convert_lesson, recent_lessons
    from .server import running_url

    folders = a.folders or [Path(r["path"]) for r in recent_lessons()]
    if not folders:
        ui.info("no recent lessons: name the folders to convert")
        return 0
    failed = 0
    for folder in folders:
        if folder.suffix == LESSON_EXT:
            ui.info(f"already a document: {folder}")
            continue
        try:
            if running_url(folder.expanduser().resolve()):
                raise AfterclassError("it is open: close its window first")
            ui.ok(f"{folder.name} → {convert_lesson(folder).name}")
        except (AfterclassError, OSError) as e:
            ui.warn(f"{folder}: {e}")
            failed += 1
    return 1 if failed else 0


def config_main(argv):
    p = argparse.ArgumentParser(prog=f"{APP_NAME} config", description="Non-secret defaults.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print the current defaults")
    sub.add_parser("path", help="print the config file path")
    s = sub.add_parser("set", help="change a default")
    s.add_argument("key", choices=list(config.DEFAULTS))
    s.add_argument("value")
    a = p.parse_args(argv)
    if a.cmd == "path":
        print(config.config_path())
    elif a.cmd == "set":
        config.set_value(a.key, a.value)
        ui.ok(f"{a.key} = {a.value}  ({config.config_path()})")
    else:
        for k, v in config.load().items():
            print(f"{k} = {v!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
