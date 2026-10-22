"""Local LLM (offline): LM Studio or Ollama on this Mac. No key, nothing leaves the machine."""

import json
import time
import urllib.error
import urllib.request

from ..config import load as load_config
from ..ui import LecapError
from .base import Provider, Usage

SERVERS = [
    ("LM Studio", "http://localhost:1234"),
    ("Ollama", "http://localhost:11434"),
]
OLLAMA_CTX = 32768  # context window lecap asks Ollama for (its default is only a few thousand tokens)


def _get(url, timeout=1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url, body, timeout=1800):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise LecapError(f"local model server error {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise LecapError(f"local model server not reachable ({e.reason}). Start it, or run `lecap setup ai`.")


def _is_chat_model(model_id):
    return not any(x in model_id.lower() for x in ("embed", "whisper", "rerank"))


def detect(url_setting="auto"):
    """Running servers: [{name, url, models: [ids]}]. url_setting = 'auto' or a base URL."""
    candidates = SERVERS if url_setting in ("", "auto") else [("custom", url_setting.rstrip("/").removesuffix("/v1"))]
    found = []
    for name, base in candidates:
        try:
            data = _get(f"{base}/v1/models")
        except Exception:
            continue
        models = [m["id"] for m in data.get("data", []) if _is_chat_model(m.get("id", ""))]
        kind = "ollama" if base.endswith(":11434") or name == "Ollama" else "openai"
        found.append({"name": name, "url": base, "kind": kind, "models": models})
    return found


class LocalProvider(Provider):
    name = "local"
    label = "Local model"
    default_model = ""  # the first loaded model
    needs_key = False

    def __init__(self):
        self._server, self._checked = None, 0.0

    def server(self):
        # Re-check every few seconds: the app may be started or stopped while `lecap open` runs
        if time.time() - self._checked > 5:
            cfg = load_config()
            found = detect(cfg["local_llm_url"])
            self._server, self._checked = (found[0] if found else None), time.time()
        return self._server

    def available(self):
        srv = self.server()
        if not srv:
            return False, "no local model server running (LM Studio or Ollama). Run `lecap setup ai`."
        if not srv["models"]:
            return False, f"{srv['name']} is running but has no chat model. Run `lecap setup ai`."
        return True, ""

    def pick_model(self, model):
        srv = self.server()
        wanted = model or load_config()["local_llm_model"]
        if wanted:
            return wanted
        if not srv or not srv["models"]:
            raise LecapError("no local chat model available. Run `lecap setup ai`.")
        return srv["models"][0]

    def describe(self, model=""):
        srv = self.server()
        return f"{srv['name']} · {self.pick_model(model)}" if srv else "local model"

    def context_limit(self, model):
        """Tokens the model can take, or None if unknown."""
        srv = self.server()
        if not srv:
            return None
        if srv["kind"] == "ollama":
            return OLLAMA_CTX
        try:  # LM Studio's native API reports the context it was loaded with
            for m in _get(f"{srv['url']}/api/v0/models").get("data", []):
                if m.get("id") == model:
                    return m.get("loaded_context_length") or m.get("max_context_length")
        except Exception:
            pass
        return None

    def run(self, system, prompt, schema, model, web=False):
        if web:
            raise LecapError("local models can't search the web")
        srv = self.server()
        if not srv:
            raise LecapError("no local model server running. Start LM Studio or Ollama, or run `lecap setup ai`.")
        model = self.pick_model(model)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if srv["kind"] == "ollama":
            out = _post(f"{srv['url']}/api/chat", {
                "model": model, "messages": messages, "stream": False, "format": schema,
                "options": {"num_ctx": OLLAMA_CTX, "temperature": 0.2},
            })
            text = out.get("message", {}).get("content", "")
            usage = Usage(out.get("prompt_eval_count", 0), out.get("eval_count", 0), None, model)
        else:
            out = _post(f"{srv['url']}/v1/chat/completions", {
                "model": model, "messages": messages, "temperature": 0.2,
                "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
            })
            choice = (out.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise LecapError("the local model ran out of context. Load it with a larger context (lecap setup ai).")
            text = choice.get("message", {}).get("content", "")
            u = out.get("usage", {})
            usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), None, model)
        try:
            return json.loads(text), usage
        except json.JSONDecodeError:
            raise LecapError("the local model returned invalid JSON (try a larger model)")
