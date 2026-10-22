"""Claude through the local Claude Code CLI in headless mode. Uses the Claude subscription, no API key."""

import json
import shutil
import subprocess

from ..ui import LecapError
from .base import Provider, Usage


class ClaudeCodeProvider(Provider):
    name = "claude-code"
    label = "Claude Code (subscription)"
    default_model = "opus"
    needs_key = False

    def available(self):
        if shutil.which("claude"):
            return True, ""
        return False, "the `claude` CLI is not installed (https://claude.com/claude-code)"

    def run(self, system, prompt, schema, model, web=False):
        # Text in, JSON out: no file or shell access; with web=True only the two web tools
        tools = ["--tools", "WebSearch", "WebFetch", "--allowedTools", "WebSearch", "WebFetch"] if web else ["--tools", ""]
        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            *tools,
            "--no-session-persistence",
            "--model", model,
            "--system-prompt", system,
        ]
        last = ""
        for _attempt in range(2):  # one retry on a failed or invalid answer
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=1800)
            try:
                out = json.loads(r.stdout)
            except json.JSONDecodeError:
                last = (r.stderr or r.stdout).strip()[-300:]
                continue
            if out.get("safety_stops"):
                # A classifier stopped the answer; the "result" is then a placeholder, never use it
                last = ("a safety classifier stopped the answer (lectures on security topics can trigger it). "
                        "Try again later, or use `--ai claude` (API), which falls back to another model automatically")
                continue
            if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
                last = str(out.get("result") or out.get("subtype"))[:300]
                continue
            u = out.get("usage", {})
            tokens_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            used_model = next(iter(out.get("modelUsage") or {}), model)
            return out["structured_output"], Usage(tokens_in, u.get("output_tokens", 0), None, used_model)
        raise LecapError(f"Claude Code did not return a result: {last}")
