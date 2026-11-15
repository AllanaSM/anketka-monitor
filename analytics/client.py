"""Provider-neutral HTTP client for structured review analysis."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime

from .schema import validate_analysis_result


class AnalyticsClientError(RuntimeError):
    """Raised when the model endpoint cannot produce a valid analysis."""


class StructuredAnalyticsClient:
    def __init__(self, endpoint, api_key="", model_name="", timeout=30, attempts=3,
                 prompt_version="v4", opener=urllib.request.urlopen, sleep=time.sleep):
        if not endpoint:
            raise ValueError("Analytics endpoint is required")
        if type(timeout) is not int or timeout < 1:
            raise ValueError("Analytics timeout must be a positive integer")
        if type(attempts) is not int or attempts < 1:
            raise ValueError("Analytics attempts must be a positive integer")
        if not prompt_version:
            raise ValueError("Analytics prompt version is required")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout
        self.attempts = attempts
        self.prompt_version = prompt_version
        self.opener = opener
        self.sleep = sleep

    @classmethod
    def from_env(cls, opener=urllib.request.urlopen, sleep=time.sleep):
        return cls(
            endpoint=os.environ.get("ANALYTICS_ENDPOINT", "").strip(),
            api_key=os.environ.get("ANALYTICS_API_KEY", "").strip(),
            model_name=os.environ.get("ANALYTICS_MODEL", "").strip(),
            timeout=int(os.environ.get("ANALYTICS_TIMEOUT", "30")),
            attempts=int(os.environ.get("ANALYTICS_ATTEMPTS", "3")),
            prompt_version=os.environ.get("ANALYTICS_PROMPT_VERSION", "v4").strip(),
            opener=opener,
            sleep=sleep,
        )

    @staticmethod
    def _json_value(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    @classmethod
    def _review_input(cls, review):
        """Select only user-review fields; replies are deliberately excluded."""
        allowed = (
            "review_id", "review_version_id", "version_no", "status", "author",
            "published_at", "observed_at", "rating", "text",
        )
        return {key: cls._json_value(review[key]) for key in allowed if key in review}

    @staticmethod
    def _faq_input(faq_items):
        allowed = ("faq_item_version_id", "question", "answer")
        return [{key: item[key] for key in allowed if key in item} for item in faq_items]

    @staticmethod
    def _dictionary_input(pain_dictionary):
        allowed = ("pain_key", "area_key", "area_label", "label", "description", "status")
        return [{key: item[key] for key in allowed if key in item} for item in pain_dictionary]

    def _payload(self, review, faq_items, pain_dictionary):
        return {
            "model": self.model_name,
            "prompt_version": self.prompt_version,
            "review": self._review_input(review),
            "faq_items": self._faq_input(faq_items),
            "pain_dictionary": self._dictionary_input(pain_dictionary),
            "response_schema": "review-analytics-v1",
        }

    def _request(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=encoded, headers=headers, method="POST")
        last_error = "unknown endpoint error"
        for attempt in range(self.attempts):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw = response.read()
                try:
                    result = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise AnalyticsClientError("Analytics endpoint returned invalid JSON") from error
                if not isinstance(result, dict):
                    raise AnalyticsClientError("Analytics endpoint must return a JSON object")
                return result
            except urllib.error.HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504}:
                    error.close()
                    raise AnalyticsClientError(f"Analytics endpoint returned HTTP {error.code}") from error
                retry_after = error.headers.get("Retry-After", "") if error.headers else ""
                wait = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                last_error = f"HTTP {error.code}"
                error.close()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = type(error).__name__
                wait = 2 ** attempt
            except AnalyticsClientError:
                raise
            if attempt + 1 < self.attempts:
                self.sleep(min(wait, 300))
        raise AnalyticsClientError(f"Analytics endpoint failed after {self.attempts} attempts: {last_error}")

    def analyze(self, review, faq_items=(), pain_dictionary=()):
        """Analyze one review version and return a validated detached result."""
        if not isinstance(review, dict) or not review.get("text", "").strip():
            raise ValueError("Review text is required for analytics")
        result = self._request(self._payload(review, faq_items, pain_dictionary))
        return validate_analysis_result(result)
