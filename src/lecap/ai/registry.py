"""Known AI providers. To add one (OpenAI, Gemini, …), write ai/<name>.py and list it here."""

from ..config import get_secret
from ..ui import LecapError
from .claude import ClaudeProvider
from .claude_code import ClaudeCodeProvider
from .local import LocalProvider

PROVIDERS = {p.name: p for p in (LocalProvider(), ClaudeProvider(), ClaudeCodeProvider())}
ONLINE = {"claude", "claude-code"}


def pick(name):
    """Resolve a provider name. 'auto' = offline first: a running local model, else the Claude API
    with a key, else Claude Code (subscription)."""
    if name in (None, "", "auto", "default"):
        if PROVIDERS["local"].available()[0]:
            name = "local"
        elif get_secret("claude")[0]:
            name = "claude"
        else:
            name = "claude-code"
    if name not in PROVIDERS:
        raise LecapError(f"unknown AI provider '{name}'. Available: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]


def is_online(provider):
    return provider.name in ONLINE
