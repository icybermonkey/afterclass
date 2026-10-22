"""Console output: step headers, explanations, timings. Colors only on a terminal."""

import os
import subprocess
import sys
import time

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code):
    return code if _TTY else ""


BOLD, DIM, GREEN, YELLOW, RED, CYAN, RESET = (
    _c("\033[1m"), _c("\033[2m"), _c("\033[32m"), _c("\033[33m"), _c("\033[31m"), _c("\033[36m"), _c("\033[0m"),
)

INDENT = "      "


class LecapError(Exception):
    """An expected failure with a message for the user (no traceback)."""


def fmt_time(seconds):
    s = int(seconds or 0)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def human_size(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def title(text):
    print(f"{BOLD}{text}{RESET}")


def info(text=""):
    print(f"{INDENT}{text}")


def explain(text):
    """Dim one-liner saying what a step does and why."""
    print(f"{INDENT}{DIM}{text}{RESET}")


def ok(text):
    print(f"{INDENT}{GREEN}✔{RESET} {text}")


def warn(text):
    print(f"{INDENT}{YELLOW}! {text}{RESET}", file=sys.stderr)


def error(text):
    print(f"\n{RED}Error: {text}{RESET}", file=sys.stderr)


def kv(key, value):
    info(f"{key + ':':<11}{value}")


class Steps:
    """Numbered step headers with per-step timing."""

    def __init__(self, total):
        self.total = total
        self.n = 0
        self.started = 0.0
        self.run_started = time.time()

    def begin(self, name, why=None):
        self.n += 1
        self.started = time.time()
        print(f"\n{BOLD}[{self.n}/{self.total}] {name}{RESET}")
        if why:
            explain(why)

    def end(self, summary=None):
        if summary:
            ok(summary)
        info(f"{DIM}step finished in {fmt_time(time.time() - self.started)}{RESET}")

    def skip(self, name, reason):
        self.n += 1
        print(f"\n{BOLD}[{self.n}/{self.total}] {name}{RESET}")
        info(f"{CYAN}↷{RESET} {reason}")

    def elapsed(self):
        return fmt_time(time.time() - self.run_started)


def run(cmd, show=True):
    """Run a command, echo it, and indent its output live. Raises LecapError on failure."""
    if show:
        explain("$ " + " ".join(_quote(c) for c in cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail = []
    buf = ""
    redraw = False  # last thing written was a \r progress frame
    # Read char-wise so tqdm progress bars (\r updates) redraw on one line
    while ch := proc.stdout.read(1):
        if ch not in "\r\n":
            buf += ch
            continue
        line, buf = buf.strip(), ""
        if not line:
            continue
        tail = (tail + [line])[-20:]
        if not show:
            continue
        progress = ch == "\r" or "%|" in line
        if not _TTY:  # piped/logged: plain lines, no progress frames
            if not progress:
                sys.stdout.write(f"{INDENT}{line}\n")
        elif progress:  # redraw in place
            sys.stdout.write(f"\r{INDENT}{DIM}{line[:150]}{RESET}\033[K")
            redraw = True
        else:
            sys.stdout.write(("\r" if redraw else "") + f"{INDENT}{DIM}{line[:150]}{RESET}\033[K\n")
            redraw = False
        sys.stdout.flush()
    if redraw:
        sys.stdout.write("\n")
    if proc.wait() != 0:
        raise LecapError(f"{cmd[0]} failed (exit {proc.returncode}):\n" + "\n".join(tail[-8:]))


def _quote(s):
    s = str(s)
    return f"'{s}'" if any(c in s for c in " $*?") else s


def notify(title, message, sound="Glass"):
    """A macOS notification (best effort; silently skipped elsewhere or when it fails)."""
    if sys.platform != "darwin":
        return
    def esc(text):  # AppleScript string literal
        return str(text).replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}" sound name "{sound}"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass
