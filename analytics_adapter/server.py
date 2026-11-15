"""HTTP adapter from the project contract to OpenRouter Chat Completions."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from analytics.schema import ANALYSIS_RESULT_SCHEMA, AnalysisValidationError, validate_analysis_result


OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class AdapterError(RuntimeError):
    """An error that can be returned to the local caller without leaking secrets."""

    def __init__(self, message, status=HTTPStatus.BAD_GATEWAY):
        super().__init__(message)
        self.status = int(status)


def load_local_env(path=None):
    """Load a local env file without overriding variables already supplied by the process."""
    path = Path(path or Path(__file__).resolve().parents[1] / ".env.local")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and name not in os.environ:
            os.environ[name] = value


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _strip_json_fence(value):
    value = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value


def _extract_model_result(response):
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise AdapterError("OpenRouter response has no assistant content") from error
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text", ""), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise AdapterError("OpenRouter assistant content is empty")
    try:
        result = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as error:
        raise AdapterError("OpenRouter assistant content is not valid JSON") from error
    try:
        return validate_analysis_result(result)
    except AnalysisValidationError as error:
        raise AdapterError(f"OpenRouter result failed analytics validation: {error}") from error


class OpenRouterAdapter:
    def __init__(self, endpoint=None, api_key=None, model=None, timeout=60,
                 opener=urllib.request.urlopen):
        self.endpoint = endpoint or os.environ.get("OPENROUTER_ENDPOINT", OPENROUTER_ENDPOINT)
        self.api_key = api_key if api_key is not None else os.environ.get("ANALYTICS_API_KEY", "")
        self.model = model if model is not None else os.environ.get("ANALYTICS_MODEL", "")
        self.timeout = int(timeout)
        self.opener = opener
        if not self.api_key:
            raise AdapterError("ANALYTICS_API_KEY is not configured", HTTPStatus.INTERNAL_SERVER_ERROR)
        if not self.model:
            raise AdapterError("ANALYTICS_MODEL is not configured", HTTPStatus.INTERNAL_SERVER_ERROR)

    @staticmethod
    def _validate_payload(payload):
        required = {"review", "faq_items", "pain_dictionary", "response_schema"}
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise AdapterError("Request does not match the analytics adapter contract", HTTPStatus.BAD_REQUEST)
        if payload["response_schema"] != "review-analytics-v1":
            raise AdapterError("Unsupported response_schema", HTTPStatus.BAD_REQUEST)
        review = payload["review"]
        if not isinstance(review, dict) or not str(review.get("text", "")).strip():
            raise AdapterError("Request review text is required", HTTPStatus.BAD_REQUEST)

    @staticmethod
    def _instructions(prompt_version):
        base = (
            "Analyze one RuStore review for the Anketka product. Return only one JSON object, "
            "with no markdown and no commentary. Follow the supplied response schema exactly. "
            "Use an existing pain_key only when the pain matches the dictionary; for a new pain, "
            "set pain_key to null and provide candidate. Match each pain to FAQ independently. "
            "Use faq_item_version_id only from the supplied FAQ items; if there is no reliable match, "
            "use faq_status=not_answered or undetermined and null FAQ id. Do not invent identifiers."
        )
        if prompt_version == "v1":
            return base
        if prompt_version not in {"v2", "v3", "v4"}:
            raise AdapterError(f"Unsupported prompt_version: {prompt_version}", HTTPStatus.BAD_REQUEST)
        calibration = (
            " Calibration rules for prompt v2:"
            " Determine tone from the wording and meaning of the review, not from the numeric rating alone."
            " Use mixed only when the review explicitly contains both meaningful positive and negative experience "
            "or attitude. A positive recommendation with a neutral caveat such as a small amount of earnings "
            "is positive, not mixed."
            " Treat a pain as a user-experienced problem, dissatisfaction, or blocked action. Do not create a pain "
            "from a neutral fact, an acceptable trade-off, or a positive reviewer's aside. In particular, a positive "
            "review saying that earnings are small does not by itself describe a pain."
            " Merge duplicate or overlapping pain statements that describe the same underlying issue in one review. "
            "For example, a decrease in paid surveys and receiving no paid surveys except express/profile surveys "
            "are one survey-availability pain, while a separate concern that the service is dishonest or pointless "
            "may remain a distinct pain."
            " Match FAQ only when the answer addresses the same user problem, not merely the same broad topic. "
            "A generic technical complaint with insufficient detail must not be forced into an unrelated FAQ; use "
            "faq_status=undetermined with a null FAQ id when the evidence is insufficient to decide whether a FAQ "
            "applies. Keep FAQ status and evidence independent for every pain."
        )
        if prompt_version == "v2":
            return base + calibration
        v3_rules = (
            " Additional calibration rules for prompt v3:"
            " Preserve distinct pains when the review explicitly describes different symptoms or blocked actions, "
            "even if they may share a technical cause. For example, a zero balance and the need to refresh a page "
            "are two pains when both are stated; do not collapse them into one vague label."
            " Do not create a separate trust or value pain from an isolated emotional label such as 'развод' or "
            "'замануха' when it only reinforces the main complaint. Create a separate trust/value pain only when the "
            "review explicitly develops that concern or questions the value of continuing to use the service."
            " Treat a brief mild evaluation such as 'нормальное приложение' as positive when no negative experience "
            "is stated; use neutral for factual or non-evaluative text without a positive or negative judgment."
            " For FAQ matching, use partially_answered when the FAQ offers relevant guidance for the same workflow "
            "or a closely related symptom, even if it does not explain the exact cause. Reserve not_answered for no "
            "meaningful guidance, and undetermined for genuinely insufficient review or FAQ evidence."
            " Set needs_review only for a material ambiguity, low-confidence interpretation, or potentially important "
            "error. Do not flag a well-supported result merely because it contains multiple pains."
            " Write summaries, areas, pain labels, descriptions, evidence, and FAQ evidence in Russian."
        )
        if prompt_version == "v3":
            return base + calibration + v3_rules
        return base + calibration + v3_rules + (
            " Additional calibration rules for prompt v4:"
            " When a blocked action and its stated technical cause, connection mode, or emotional description are "
            "part of the same failure, keep them as one pain. For example, inability to log in with Wi-Fi not working, "
            "or inability to log in because registration is frustrating, should normally remain one authentication pain."
            " Do not set needs_review merely because a clear, specific complaint has no matching FAQ. A well-supported "
            "pain with faq_status=not_answered or undetermined may still have needs_review=false."
        )

    def _prompt(self, payload):
        schema = _json_text(ANALYSIS_RESULT_SCHEMA)
        prompt_version = str(payload.get("prompt_version", "v1")).strip() or "v1"
        instructions = self._instructions(prompt_version)
        user_payload = {
            "review": payload["review"],
            "faq_items": payload["faq_items"],
            "pain_dictionary": payload["pain_dictionary"],
            "response_schema": payload["response_schema"],
            "prompt_version": prompt_version,
            "json_schema": ANALYSIS_RESULT_SCHEMA,
        }
        return [
            {"role": "system", "content": f"{instructions}\nJSON schema:\n{schema}"},
            {"role": "user", "content": _json_text(user_payload)},
        ]

    def analyze(self, payload):
        self._validate_payload(payload)
        request_body = {
            "model": self.model,
            "messages": self._prompt(payload),
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Title": "Anketka Monitor analytics adapter",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = error.code if error.code in {400, 401, 403, 408, 429, 500, 502, 503, 504} else 502
            raise AdapterError(f"OpenRouter returned HTTP {error.code}", status) from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise AdapterError(f"OpenRouter connection failed: {type(error).__name__}") from error
        try:
            response_json = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AdapterError("OpenRouter returned invalid JSON") from error
        return _extract_model_result(response_json)


class AdapterHandler(BaseHTTPRequestHandler):
    adapter = None

    def _write_json(self, status, value):
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self._write_json(200, {"status": "ok", "provider": "openrouter"})
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/analyze":
            self._write_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise AdapterError("Request body size is invalid", HTTPStatus.BAD_REQUEST)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = self.adapter.analyze(payload)
            self._write_json(200, result)
        except AdapterError as error:
            self._write_json(error.status, {"error": str(error)})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(400, {"error": "Request body must be valid JSON"})
        except Exception:
            self._write_json(500, {"error": "Adapter failed unexpectedly"})

    def log_message(self, format_string, *args):
        print(f"[analytics-adapter] {format_string % args}", flush=True)


def run(host=None, port=None):
    load_local_env()
    host = host or os.environ.get("ADAPTER_HOST", "127.0.0.1")
    port = int(port or os.environ.get("ADAPTER_PORT", "8090"))
    AdapterHandler.adapter = OpenRouterAdapter()
    server = ThreadingHTTPServer((host, port), AdapterHandler)
    print(f"[analytics-adapter] listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[analytics-adapter] stopped", flush=True)
    finally:
        server.server_close()
