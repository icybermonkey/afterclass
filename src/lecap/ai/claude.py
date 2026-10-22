"""Claude via the Anthropic API (API key, pay per use)."""

from ..config import ant_profile_active, get_secret
from ..ui import LecapError
from .base import Provider, Usage

# USD per million tokens (input, output)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
# Models that accept the server-side refusal fallback
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EXPECTED_OUTPUT = 10_000  # tokens incl. thinking, for the cost preview


def cost(model, tokens_in, tokens_out):
    p = PRICES.get(model)
    return round((tokens_in * p[0] + tokens_out * p[1]) / 1e6, 4) if p else None


class ClaudeProvider(Provider):
    name = "claude"
    label = "Claude API"
    default_model = "claude-opus-5-5"
    needs_key = True

    def _client(self):
        try:
            import anthropic
        except ImportError:
            raise LecapError('the Claude API provider needs the anthropic package: uv tool install "lecap[claude]"')
        key, _ = get_secret("claude")
        # Without a key the SDK falls back to an `ant auth login` profile
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    def available(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, 'install the extra: uv tool install --reinstall "lecap[claude]"'
        if get_secret("claude")[0] or ant_profile_active():
            return True, ""
        return False, "no API key: run `lecap auth set claude` (key from console.anthropic.com)"

    def estimate(self, system, prompt, model):
        client = self._client()
        try:
            n = client.messages.count_tokens(model=model, system=system,
                                             messages=[{"role": "user", "content": prompt}]).input_tokens
        except Exception as e:
            raise LecapError(f"Claude API: {_describe(e)}")
        return Usage(input_tokens=n, output_tokens=EXPECTED_OUTPUT, cost_usd=cost(model, n, EXPECTED_OUTPUT), model=model)

    def run(self, system, prompt, schema, model):
        import json

        client = self._client()
        body = {"output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}}
        betas = []
        if model in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            betas.append(FALLBACK_BETA)
        try:
            with client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                betas=betas,
                extra_body=body,
            ) as stream:
                msg = stream.get_final_message()
        except Exception as e:
            raise LecapError(f"Claude API: {_describe(e)}")
        if msg.stop_reason == "refusal":
            raise LecapError("Claude declined this request (refusal)")
        if msg.stop_reason == "max_tokens":
            raise LecapError("Claude's answer was cut off (max_tokens)")
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise LecapError("Claude returned invalid JSON")
        u = msg.usage
        return data, Usage(u.input_tokens, u.output_tokens, cost(msg.model, u.input_tokens, u.output_tokens), msg.model)


def _describe(e):
    """Error text without request details (headers could carry the key)."""
    status = getattr(e, "status_code", None)
    if status == 401:
        return "the API key was rejected (401). Check it with `lecap auth status`."
    if status == 429:
        return "rate limit or spend limit reached (429). Try again later."
    message = getattr(e, "message", None) or e.__class__.__name__
    return f"{status} {message}" if status else str(message)
