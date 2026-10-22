"""Non-secret defaults in ~/.config/lecap/config.toml, secrets in env vars or the macOS Keychain.

Secrets are never written to the config file, the output folder or logs.
"""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import APP_NAME

DEFAULTS = {
    "language": "en",
    "threshold": 4.0,
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "media": "copy",  # copy | link | move
    "speakers": True,  # tell lecturer and other speakers apart
    "transcript_view": "paragraphs",  # paragraphs | blocks
    "ai_provider": "auto",  # auto = local LLM if running, else claude with a key, else claude-code
    "ai_model": "",  # empty = the provider's default
    "ask_provider": "auto",
    "ask_model": "",  # empty = the provider's default (or the first loaded local model)
    "local_llm_url": "auto",  # auto = LM Studio, then Ollama; or an OpenAI-compatible base URL
    "local_llm_model": "",  # empty = the first loaded chat model
    "allow_online": "ask",  # off | ask | on: may Ask use an online model for questions beyond the lesson
    "allow_web": "ask",  # off | ask: may Ask search the web (always needs a click)
}

CHOICES = {
    "media": ("copy", "link", "move"),
    "transcript_view": ("paragraphs", "blocks"),
    "ai_provider": ("auto", "local", "claude", "claude-code"),
    "ask_provider": ("auto", "local", "claude", "claude-code"),
    "allow_online": ("off", "ask", "on"),
    "allow_web": ("off", "ask"),
}

# Dropdown options for free-text settings (a custom value stays possible)
SUGGESTIONS = {
    "whisper_model": [
        ("mlx-community/whisper-large-v3-turbo", "large-v3-turbo · fast and accurate (default)"),
        ("mlx-community/whisper-large-v3-mlx", "large-v3 · most accurate, about 2× slower"),
        ("mlx-community/whisper-medium-mlx", "medium · faster, less accurate"),
        ("mlx-community/whisper-small-mlx", "small · fastest, least accurate"),
    ],
    "local_llm_url": [
        ("auto", "automatic (LM Studio, then Ollama)"),
        ("http://localhost:1234", "LM Studio"),
        ("http://localhost:11434", "Ollama"),
    ],
}

HELP = {
    "language": "Spoken language of the lectures (en, de, …)",
    "threshold": "Slide change sensitivity; higher = fewer cards",
    "whisper_model": "Speech-to-text model (mlx-whisper)",
    "media": "Video and slides (mp4, pdf, pptx): copy them into the lesson folder, link to the originals, or move them",
    "speakers": "Tell the lecturer and other speakers apart (offline)",
    "transcript_view": "Default transcript view on the page",
    "ai_provider": "Model for the --ai study extras",
    "ai_model": "Model name for --ai (empty = provider default)",
    "ask_provider": "Model for the Ask box under the video",
    "ask_model": "Model name for Ask (empty = default; also set from the picker in the Ask box)",
    "local_llm_url": "Local AI server (auto = LM Studio, then Ollama)",
    "local_llm_model": "Local model (empty = the first loaded one)",
    "allow_online": "Ask may use online Claude for questions beyond the lesson",
    "allow_web": "Ask may offer a web search (always needs your click)",
}

# Provider name -> environment variable holding its key
KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
}


def config_path():
    if p := os.environ.get(f"{APP_NAME.upper()}_CONFIG"):
        return Path(p)
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME / "config.toml"


def load():
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        with path.open("rb") as f:
            for k, v in tomllib.load(f).items():
                if k in DEFAULTS:
                    cfg[k] = v
    return cfg


def coerce(key, raw):
    """Convert a raw value (CLI string or JSON value) to the key's type. Raises ValueError."""
    if key not in DEFAULTS:
        raise KeyError(key)
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            value = raw
        elif str(raw).lower() in ("1", "true", "yes", "on"):
            value = True
        elif str(raw).lower() in ("0", "false", "no", "off"):
            value = False
        else:
            raise ValueError(f"{key} must be true or false")
    elif isinstance(default, float):
        value = float(raw)
    else:
        value = str(raw).strip()
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of: {', '.join(CHOICES[key])}")
    return value


def set_value(key, raw):
    cfg = load()
    cfg[key] = coerce(key, raw)
    save(cfg)


def save(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {APP_NAME} defaults. No secrets here: use `{APP_NAME} auth set <provider>`."]
    for k in DEFAULTS:
        v = cfg.get(k, DEFAULTS[k])
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, str):
            lines.append(f'{k} = "{_toml_escape(v)}"')
        else:
            lines.append(f"{k} = {v!r}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def _toml_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# --- secrets -------------------------------------------------------------


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        return None


def get_secret(provider):
    """Return (key, source) or (None, None). Order: env var, then Keychain."""
    env = KEY_ENV.get(provider)
    if env and os.environ.get(env):
        return os.environ[env], f"env ${env}"
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(APP_NAME, provider)
        except Exception:
            value = None
        if value:
            return value, "macOS Keychain"
    return None, None


def set_secret(provider, value):
    kr = _keyring()
    if not kr:
        raise RuntimeError("the keyring package is not available")
    kr.set_password(APP_NAME, provider, value)


def delete_secret(provider):
    kr = _keyring()
    if not kr:
        return False
    try:
        kr.delete_password(APP_NAME, provider)
        return True
    except Exception:
        return False


def mask(secret):
    if not secret:
        return ""
    return f"{secret[:7]}…{secret[-4:]}" if len(secret) > 14 else "…" + secret[-2:]


def ant_profile_active():
    """True when the Anthropic CLI (`ant auth login`) has an active profile the SDK can use."""
    if not shutil.which("ant"):
        return False
    try:
        r = subprocess.run(["ant", "auth", "status"], capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return r.returncode == 0 and "no active" not in (r.stdout + r.stderr).lower()
