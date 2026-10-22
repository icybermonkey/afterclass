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
    "ai_provider": "auto",  # auto = claude with a key, else claude-code
    "ai_model": "",  # empty = the provider's default
    "media": "copy",  # copy | link | move
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


def set_value(key, raw):
    if key not in DEFAULTS:
        raise KeyError(key)
    cfg = {k: v for k, v in load().items()}
    default = DEFAULTS[key]
    cfg[key] = type(default)(raw) if not isinstance(default, str) else raw
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {APP_NAME} defaults. No secrets here: use `{APP_NAME} auth set <provider>`."]
    for k, v in cfg.items():
        lines.append(f"{k} = {v!r}" if not isinstance(v, str) else f'{k} = "{_toml_escape(v)}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
