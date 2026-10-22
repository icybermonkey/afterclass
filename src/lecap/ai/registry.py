"""Known AI providers. To add one (OpenAI, Gemini, Ollama, ...), write ai/<name>.py and list it here."""

from ..config import get_secret
from ..ui import LecapError
from .claude import ClaudeProvider
from .claude_code import ClaudeCodeProvider

PROVIDERS = {p.name: p for p in (ClaudeProvider(), ClaudeCodeProvider())}


def pick(name):
    """Resolve a provider name; 'auto' = Claude API when a key exists, else Claude Code."""
    if name in (None, "", "auto"):
        name = "claude" if get_secret("claude")[0] else "claude-code"
    if name not in PROVIDERS:
        raise LecapError(f"unknown AI provider '{name}'. Available: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]
