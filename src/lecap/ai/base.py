"""The interface every AI provider implements. Add a provider = one file + one registry line."""

from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    model: str = ""


class Provider:
    name = ""
    label = ""
    default_model = ""
    needs_key = False  # True: uses `lecap auth set <name>` / an env var

    def available(self):
        """(ok, reason). reason says how to fix it when not ok."""
        raise NotImplementedError

    def estimate(self, system, prompt, model):
        """Optional cost preview: Usage with input_tokens and cost_usd, or None."""
        return None

    def run(self, system, prompt, schema, model, web=False):
        """Return (dict matching the schema, Usage). web=True may search the web (online providers only)."""
        raise NotImplementedError
