"""Validation contract for one structured review analysis result."""

from __future__ import annotations

from copy import deepcopy


class AnalysisValidationError(ValueError):
    """Raised when a model result does not match the analytics contract."""


ANALYSIS_RESULT_SCHEMA = {
    "type": "object",
    "required": ["tone", "summary", "areas", "pains", "confidence", "needs_review"],
    "additionalProperties": False,
    "properties": {
        "tone": {"type": "string", "enum": ["positive", "mixed", "neutral", "negative"]},
        "summary": {"type": "string", "minLength": 1},
        "areas": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "pains": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "pain_key", "candidate", "intensity", "pain_evidence",
                    "confidence", "faq_status", "faq_item_version_id", "faq_evidence",
                ],
                "additionalProperties": False,
                "properties": {
                    "pain_key": {"type": ["string", "null"]},
                    "candidate": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": ["area_key", "area_label", "label", "description"],
                        "properties": {
                            "area_key": {"type": "string", "minLength": 1},
                            "area_label": {"type": "string", "minLength": 1},
                            "label": {"type": "string", "minLength": 1},
                            "description": {"type": "string", "minLength": 1},
                        },
                    },
                    "intensity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "pain_evidence": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "faq_status": {
                        "type": "string",
                        "enum": ["answered", "partially_answered", "not_answered", "undetermined"],
                    },
                    "faq_item_version_id": {"type": ["integer", "null"], "minimum": 1},
                    "faq_evidence": {"type": ["string", "null"]},
                },
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_review": {"type": "boolean"},
    },
}


def _fail(path, message):
    raise AnalysisValidationError(f"{path}: {message}")


def _string(value, path, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _fail(path, "must be a non-empty string")


def _confidence(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        _fail(path, "must be a number from 0 to 1")


def _keys(value, required, path):
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    actual = set(value)
    missing = set(required) - actual
    extra = actual - set(required)
    if missing:
        _fail(path, f"missing keys: {', '.join(sorted(missing))}")
    if extra:
        _fail(path, f"unexpected keys: {', '.join(sorted(extra))}")


def validate_analysis_result(value):
    """Validate and return a detached result suitable for persistence."""
    required = ["tone", "summary", "areas", "pains", "confidence", "needs_review"]
    _keys(value, required, "result")
    if value["tone"] not in {"positive", "mixed", "neutral", "negative"}:
        _fail("result.tone", "has an unsupported value")
    _string(value["summary"], "result.summary")
    if not isinstance(value["areas"], list):
        _fail("result.areas", "must be an array")
    for index, area in enumerate(value["areas"]):
        _string(area, f"result.areas[{index}]")
    if len(value["areas"]) != len(set(value["areas"])):
        _fail("result.areas", "must not contain duplicates")
    if not isinstance(value["pains"], list):
        _fail("result.pains", "must be an array")
    _confidence(value["confidence"], "result.confidence")
    if not isinstance(value["needs_review"], bool):
        _fail("result.needs_review", "must be boolean")

    pain_required = [
        "pain_key", "candidate", "intensity", "pain_evidence", "confidence",
        "faq_status", "faq_item_version_id", "faq_evidence",
    ]
    for index, pain in enumerate(value["pains"]):
        path = f"result.pains[{index}]"
        _keys(pain, pain_required, path)
        if pain["pain_key"] is not None:
            _string(pain["pain_key"], f"{path}.pain_key")
        candidate = pain["candidate"]
        if candidate is not None:
            candidate_required = ["area_key", "area_label", "label", "description"]
            _keys(candidate, candidate_required, f"{path}.candidate")
            for key in candidate_required:
                _string(candidate[key], f"{path}.candidate.{key}")
        if pain["pain_key"] is None and candidate is None:
            _fail(path, "must provide pain_key or candidate")
        if pain["pain_key"] is not None and candidate is not None:
            _fail(path, "must not provide candidate for an existing pain_key")
        if pain["intensity"] not in {"low", "medium", "high"}:
            _fail(f"{path}.intensity", "has an unsupported value")
        _string(pain["pain_evidence"], f"{path}.pain_evidence")
        _confidence(pain["confidence"], f"{path}.confidence")
        if pain["faq_status"] not in {"answered", "partially_answered", "not_answered", "undetermined"}:
            _fail(f"{path}.faq_status", "has an unsupported value")
        faq_version = pain["faq_item_version_id"]
        if faq_version is not None and (isinstance(faq_version, bool) or not isinstance(faq_version, int) or faq_version < 1):
            _fail(f"{path}.faq_item_version_id", "must be a positive integer or null")
        if pain["faq_evidence"] is not None:
            _string(pain["faq_evidence"], f"{path}.faq_evidence")

    return deepcopy(value)
