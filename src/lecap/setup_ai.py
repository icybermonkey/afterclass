"""`lecap setup ai`: guided first-time setup of a local model (LM Studio or Ollama).

Nothing is downloaded or started without a yes. Also used by the settings page (status()).
"""

import json
import shutil
import urllib.request
import subprocess
import sys
import time
from pathlib import Path

from . import APP_NAME, config, ui
from .ai.local import LocalProvider, detect
from .ui import LecapError

LMS_FALLBACK = Path.home() / ".lmstudio" / "bin" / "lms"
CONTEXT = 32768  # enough for a 2.5 h lecture in one go

# Models to offer, best first per memory size (checked against the LM Studio / Ollama catalogs, Oct 2026).
# (min RAM GB, LM Studio key, Ollama tag or None, download GB, active params in billions, why)
# MoE models ("a4b", "a3b") only use a few billion parameters per word: big-model quality at small-model speed.
# "-qat" versions are trained for compression: same size, better quality.
MODELS = [
    (32, "google/gemma-4-26b-a4b-qat", "gemma4:26b", 15.6, 3.8, "fast (MoE) and strong, 256K context"),
    (32, "qwen/qwen3.6-35b-a3b", "qwen3.6:35b", 20.4, 3.0, "very good and fast (MoE), thinks before answering; tight on 32 GB"),
    (32, "google/gemma-4-31b-qat", "gemma4:31b", 19.0, 30.7, "best Gemma quality, but dense: much slower"),
    (16, "google/gemma-4-12b-qat", "gemma4:12b", 7.4, 12.0, "good; the pick for 16 GB"),
    (16, "qwen/qwen3.5-9b", None, 7.0, 9.0, "good, thinks before answering"),
    (0, "google/gemma-4-e4b-qat", "gemma4:e4b", 5.9, 4.5, "small and fast; weaker summaries"),
]

# Memory bandwidth (GB/s) decides how fast a local model writes on Apple Silicon
BANDWIDTH = {"M1": 68, "M2": 100, "M3": 100, "M4": 120, "M1 Pro": 200, "M2 Pro": 200, "M3 Pro": 150, "M4 Pro": 273,
             "M1 Max": 400, "M2 Max": 400, "M3 Max": 400, "M4 Max": 546, "M1 Ultra": 800, "M2 Ultra": 800, "M3 Ultra": 819}


def mac():
    """This Mac: chip, memory, free disk and memory bandwidth (for speed estimates)."""
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    except Exception:
        chip = ""
    family = chip.replace("Apple ", "")
    bw = next((v for k, v in sorted(BANDWIDTH.items(), key=lambda kv: -len(kv[0])) if family.startswith(k)), 100)
    free = shutil.disk_usage(Path.home()).free / 1e9
    return {"chip": chip or "this Mac", "ram_gb": round(ram_gb()), "free_gb": round(free), "bandwidth": bw}


def words_per_second(active_b, bandwidth):
    """Rough writing speed (an estimate, not a benchmark): each token reads the active weights once
    (4-bit ≈ 0.6 GB per billion parameters), at ~50 % of the memory bandwidth; ~0.75 words per token."""
    return round(bandwidth / (active_b * 0.6) * 0.5 * 0.75)


def ram_gb():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
    except Exception:
        return 0


def choices(ram=None, app="lmstudio", info=None):
    """Models that fit this Mac's memory, best first: [(name, download GB, why)]. With info (from mac()),
    'why' gets a speed estimate and a warning when the download doesn't fit on the disk."""
    ram = ram_gb() if ram is None else ram
    out = []
    for min_ram, lms_key, ollama_tag, gb, active, why in MODELS:
        name = lms_key if app == "lmstudio" else ollama_tag
        if not name or ram < min_ram:
            continue
        if info:
            why = f"{why}; ~{words_per_second(active, info['bandwidth'])} words/s on your {info['chip'].replace('Apple ', '')}"
            if gb + 3 > info["free_gb"]:
                why += f"; ⚠ only {info['free_gb']} GB free disk"
        out.append((name, gb, why))
    return out


def recommend(ram=None, app="lmstudio", info=None):
    """The best model that also fits on the disk (if known)."""
    offer = choices(ram, app, info)
    return next((c for c in offer if "⚠" not in c[2]), offer[-1])


def lms_bin():
    return shutil.which("lms") or (str(LMS_FALLBACK) if LMS_FALLBACK.exists() else None)


def installed_models(app):
    """[(name, size_gb)] of chat models on disk."""
    try:
        if app == "lmstudio" and lms_bin():
            out = subprocess.run([lms_bin(), "ls", "--llm", "--json"], capture_output=True, text=True, timeout=20).stdout
            return [(m["modelKey"], round(m.get("sizeBytes", 0) / 1e9, 1)) for m in json.loads(out or "[]")]
        if app == "ollama" and shutil.which("ollama"):
            out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20).stdout
            rows = [line.split() for line in out.splitlines()[1:] if line.strip()]
            return [(r[0], _gb(r[2], r[3]) if len(r) > 3 else 0) for r in rows if "embed" not in r[0]]
    except Exception:
        pass
    return []


def _gb(num, unit):
    try:
        return round(float(num) * (1 if unit.upper().startswith("G") else 0.001), 1)
    except ValueError:
        return 0


ALLOW_PAGE = {  # let lesson pages opened as files (origin "null") talk to the model server
    "lmstudio": "lms server stop && lms server start --cors",
    "ollama": "launchctl setenv OLLAMA_ORIGINS null && brew services restart ollama",
}


def page_allowed(url):
    """True if the model server answers browser requests from a file:// page (CORS)."""
    try:
        req = urllib.request.Request(f"{url}/v1/models", headers={"Origin": "null"})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            allow = r.headers.get("Access-Control-Allow-Origin", "")
        return allow in ("*", "null")
    except Exception:
        return False


def status():
    """Everything the settings page shows about local AI."""
    running = {s["name"]: s for s in detect("auto")}
    apps = []
    for app, name, binary in (("lmstudio", "LM Studio", lms_bin()), ("ollama", "Ollama", shutil.which("ollama"))):
        srv = running.get(name)
        apps.append({
            "app": app, "name": name, "installed": bool(binary),
            "running": bool(srv), "loaded": (srv or {}).get("models", []),
            "models": [{"name": n, "gb": gb} for n, gb in installed_models(app)] if binary else [],
            "page_allowed": page_allowed(srv["url"]) if srv else False, "allow_cmd": ALLOW_PAGE[app],
        })
    info = mac()
    ram = info["ram_gb"]
    lm, ol = recommend(ram, "lmstudio", info), recommend(ram, "ollama", info)
    return {"apps": apps, "ram_gb": ram, "mac": info,
            "recommended": {"lmstudio": lm[0], "ollama": ol[0], "gb": lm[1], "why": lm[2]},
            "choices": {"lmstudio": [{"name": n, "gb": g, "why": w} for n, g, w in choices(ram, "lmstudio", info)],
                        "ollama": [{"name": n, "gb": g, "why": w} for n, g, w in choices(ram, "ollama", info)]}}


# --- the wizard ------------------------------------------------------------


def _ask(question, default=False):
    if not sys.stdin.isatty():
        return False
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{ui.INDENT}{question} {hint} ").strip().lower()
    return default if not answer else answer in ("y", "yes", "j", "ja")


def _choose(question, options):
    """options: [(key, label)]; a key of None is a group heading (not numbered). Returns a key or None."""
    keys = []
    for key, label in options:
        if key is None:
            ui.info(f"{ui.BOLD}{label}{ui.RESET}")
            continue
        keys.append(key)
        ui.info(f"  {len(keys)}) {label}")
    if not sys.stdin.isatty():
        return None
    answer = input(f"{ui.INDENT}{question} [1-{len(keys)}] ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(keys):
        return keys[int(answer) - 1]
    return None


def family(name):
    n = name.lower()
    return "Gemma (Google)" if "gemma" in n else "Qwen (Alibaba)" if "qwen" in n else "Other models"


def _run(cmd):
    ui.explain("$ " + " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise LecapError(f"{cmd[0]} failed (exit {r.returncode})")


def wizard():
    steps = ui.Steps(6)
    ui.title(f"{APP_NAME}: set up a local AI model (offline)")
    ui.explain("A local model makes --ai and the Ask tab work without internet. Nothing is downloaded or "
               "started without your yes.")

    steps.begin("Look for local AI apps")
    st = status()
    for a in st["apps"]:
        if not a["installed"]:
            ui.info(f"✗ {a['name']}: not installed")
            continue
        models = ", ".join(f"{m['name']} ({m['gb']} GB)" for m in a["models"]) or "no models yet"
        ui.info(f"✔ {a['name']}: server {'running' if a['running'] else 'off'}; models: {models}")
    installed = [a for a in st["apps"] if a["installed"]]
    if not installed:
        ui.info("Install one of them first (both are free):")
        ui.info("  LM Studio: https://lmstudio.ai  (an app with a model browser; easiest)")
        ui.info("  Ollama:    brew install ollama   (runs in the background, terminal only)")
        steps.end()
        return 1
    m = st["mac"]
    ui.info(f"your Mac: {m['chip']} · {m['ram_gb']} GB memory · {m['free_gb']} GB free disk · "
            f"memory speed ~{m['bandwidth']} GB/s (decides how fast local models write)")
    steps.end()

    steps.begin("Choose the app", "Recommended on Apple Silicon: LM Studio. It runs MLX models (made for Macs, usually "
                "faster), has a model browser, and can allow Ask from lesson pages. Ollama works too.")
    if len(installed) == 1:
        app = installed[0]
    else:
        installed.sort(key=lambda a: (a["app"] != "lmstudio", not a["models"], not a["running"]))
        key = _choose("Which app should lecap use?", [(a["app"], f"{a['name']}" + (" (recommended)" if a["app"] == "lmstudio" else "")
                                                       + (" · has models" if a["models"] else "")) for a in installed])
        app = next((a for a in installed if a["app"] == key), installed[0])
    steps.end(f"using {app['name']}")

    steps.begin("Start its server", "lecap talks to the app over a local server on this Mac (no internet).")
    if app["running"]:
        steps.end("already running")
    elif _ask(f"Start the {app['name']} server now?", default=True):
        if app["app"] == "lmstudio":
            _run([lms_bin(), "server", "start"])
        elif shutil.which("brew"):
            _run(["brew", "services", "start", "ollama"])
        else:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        for _ in range(20):
            if any(s["name"] == app["name"] for s in detect("auto")):
                break
            time.sleep(0.5)
        steps.end("started")
    else:
        ui.info("start it later with: " + ("lms server start" if app["app"] == "lmstudio" else "brew services start ollama"))
        steps.end()
        return 1

    steps.begin("Choose a model")
    offer = choices(st["ram_gb"], app["app"], m)
    best = recommend(st["ram_gb"], app["app"], m)
    ui.explain(f"For your {m['chip']} with {m['ram_gb']} GB I recommend {best[0]} (~{best[1]} GB): {best[2]}. "
               "Bigger models write better titles and answers but need more memory and time.")
    installed = {x["name"] for x in app["models"]}
    star = f"{ui.GREEN}{ui.BOLD}★ recommended{ui.RESET} "
    options = []
    if app["models"]:
        options.append((None, "Installed"))
        options += [(x["name"], f"use {x['name']} ({x['gb']} GB)") for x in app["models"]]
    # Downloads grouped by model family; the recommended one first in its group and marked with ★
    downloads = [c for c in offer if c[0] not in installed]
    downloads.sort(key=lambda c: c[0] != best[0])
    for fam in sorted({family(c[0]) for c in downloads}, key=lambda f: f != family(best[0])):
        options.append((None, f"Download · {fam}"))
        for name, gb, why in downloads:
            if family(name) == fam:
                options.append((f"dl:{name}", f"{star if name == best[0] else ''}{name} (~{gb} GB): {why}"))
    options.append((None, "Something else"))
    options.append(("__other__", "type a model name (from lmstudio.ai/models or ollama.com/library)"))
    choice = _choose("Which model?", options)
    if choice is None:
        ui.info("nothing chosen")
        steps.end()
        return 1
    model, size = choice, None
    if choice == "__other__":
        model = input(f"{ui.INDENT}Model name: ").strip() if sys.stdin.isatty() else ""
        if not model:
            steps.end()
            return 1
        choice = f"dl:{model}" if model not in installed else model
    if choice.startswith("dl:"):
        model = choice[3:]
        size = next((f"~{gb} GB" for n, gb, _ in offer if n == model), "size: see the catalog")
        size += f"; {m['free_gb']} GB free on your disk"
        if not _ask(f"Download {model} now ({size})?"):
            ui.info("download later with: " + (f"lms get {model}" if app["app"] == "lmstudio" else f"ollama pull {model}"))
            steps.end()
            return 1
        _run([lms_bin(), "get", model, "-y"] if app["app"] == "lmstudio" else ["ollama", "pull", model])
    if app["app"] == "lmstudio":
        ui.explain(f"LM Studio loads models with a small context by default; lecap needs {CONTEXT:,} tokens "
                   "to read a whole lecture at once.")
        others = [x for x in app["loaded"] if x != model]
        if others and _ask(f"Unload {', '.join(others)} first to free memory?", default=True):
            _run([lms_bin(), "unload", "--all"])
        if _ask(f"Load {model} with a {CONTEXT:,}-token context now?", default=True):
            _run([lms_bin(), "load", model, "--context-length", str(CONTEXT), "-y"])
    steps.end(f"model: {model}")

    steps.begin("Test it", "A short request with structured output, like the real thing.")
    url = "http://localhost:1234" if app["app"] == "lmstudio" else "http://localhost:11434"
    cfg = config.load()
    cfg["local_llm_url"], cfg["local_llm_model"] = url, model
    config.save(cfg)
    provider = LocalProvider()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
              "additionalProperties": False}
    t = time.time()
    data, usage = provider.run("Answer briefly.", "In one sentence: what is an opaque predicate?", schema, model)
    dt = time.time() - t
    ui.info(f"answer: {data.get('answer', '')[:160]}")
    speed = f", {usage.output_tokens / dt:.0f} tokens/s" if usage.output_tokens else ""
    steps.end(f"works ({dt:.1f} s{speed}). Saved to {config.config_path()}")

    steps.begin("Ask from a lesson page opened as a file (optional)",
                f"With `{APP_NAME} open <folder>` Ask always works. To also use Ask when you just double-click "
                "index.html, the model server must allow browser pages.")
    if page_allowed(url):
        steps.end("already allowed")
    else:
        if app["app"] == "lmstudio":
            ui.explain("Note: LM Studio's switch allows ANY website you visit to use your local model (not your "
                       "files). To keep it after a restart: LM Studio → Developer → Server Settings → Enable CORS.")
        else:
            ui.explain("This allows only pages opened as files (origin 'null') to use Ollama.")
        if _ask("Allow it now?"):
            if app["app"] == "lmstudio":
                _run([lms_bin(), "server", "stop"])
                _run([lms_bin(), "server", "start", "--cors"])
            else:
                _run(["launchctl", "setenv", "OLLAMA_ORIGINS", "null"])
                if shutil.which("brew"):
                    _run(["brew", "services", "restart", "ollama"])
            steps.end("allowed" if page_allowed(url) else "done; if Ask still can't reach it, restart the app")
        else:
            steps.end(f"skipped; later: {ALLOW_PAGE[app['app']]}")
    ui.info(f"Use it: `{APP_NAME} <video> <out> --ai` (local is the default when it runs) and the Ask box.")
    return 0
