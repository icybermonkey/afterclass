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

# (min RAM GB, LM Studio key, Ollama name, download GB, why)
RECOMMENDED = [
    (32, "qwen/qwen3-14b", "qwen3:14b", 9, "best quality that runs comfortably with 32 GB"),
    (16, "qwen/qwen3-8b", "qwen3:8b", 5, "good balance for 16 GB"),
    (0, "qwen/qwen3-4b", "qwen3:4b", 3, "small and fast for 8 GB Macs"),
]


def ram_gb():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
    except Exception:
        return 0


def recommend(ram=None):
    ram = ram_gb() if ram is None else ram
    return next(r for r in RECOMMENDED if ram >= r[0])


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
    rec = recommend()
    return {"apps": apps, "ram_gb": round(ram_gb()), "recommended": {"lmstudio": rec[1], "ollama": rec[2],
                                                                    "gb": rec[3], "why": rec[4]}}


# --- the wizard ------------------------------------------------------------


def _ask(question, default=False):
    if not sys.stdin.isatty():
        return False
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{ui.INDENT}{question} {hint} ").strip().lower()
    return default if not answer else answer in ("y", "yes", "j", "ja")


def _choose(question, options):
    """options: [(key, label)]. Returns a key or None."""
    for i, (_k, label) in enumerate(options, 1):
        ui.info(f"  {i}) {label}")
    if not sys.stdin.isatty():
        return None
    answer = input(f"{ui.INDENT}{question} [1-{len(options)}] ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][0]
    return None


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
    steps.end(f"your Mac has {st['ram_gb']} GB RAM")

    steps.begin("Choose the app")
    if len(installed) == 1:
        app = installed[0]
    else:
        # Prefer the one that already has models or is running
        installed.sort(key=lambda a: (not a["models"], not a["running"]))
        key = _choose("Which app should lecap use?", [(a["app"], f"{a['name']}" + (" (has models)" if a["models"] else ""))
                                                     for a in installed])
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
    rec = recommend()
    rec_name = rec[1] if app["app"] == "lmstudio" else rec[2]
    ui.explain(f"Recommended for {st['ram_gb']} GB: {rec_name} (~{rec[3]} GB download), {rec[4]}. "
               "Smaller models are faster but write weaker summaries.")
    options = [(m["name"], f"use {m['name']} ({m['gb']} GB, installed)") for m in app["models"]]
    if rec_name not in {m["name"] for m in app["models"]}:
        options.append(("__download__", f"download {rec_name} (~{rec[3]} GB)"))
    choice = _choose("Which model?", options) if options else "__download__"
    if choice is None:
        ui.info("nothing chosen")
        steps.end()
        return 1
    model = choice
    if choice == "__download__":
        model = rec_name
        if not _ask(f"Download {model} now (~{rec[3]} GB)?"):
            ui.info("download later with: " + (f"lms get {model}" if app["app"] == "lmstudio" else f"ollama pull {model}"))
            steps.end()
            return 1
        _run([lms_bin(), "get", model, "-y"] if app["app"] == "lmstudio" else ["ollama", "pull", model])
    if app["app"] == "lmstudio":
        ui.explain(f"LM Studio loads models with a small context by default; lecap needs {CONTEXT:,} tokens "
                   "to read a whole lecture at once.")
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
