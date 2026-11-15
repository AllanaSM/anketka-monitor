"""Independent FAQ matching for each pain in an analysis result."""

from __future__ import annotations

from .schema import validate_analysis_result


FAQ_STATUSES = {"answered", "partially_answered", "not_answered", "undetermined"}


def _validate_match(match, path):
    if not isinstance(match, dict):
        raise ValueError(f"{path}: FAQ match must be an object")
    required = {"faq_status", "faq_item_version_id", "faq_evidence"}
    if set(match) != required:
        raise ValueError(f"{path}: FAQ match keys must be {sorted(required)}")
    if match["faq_status"] not in FAQ_STATUSES:
        raise ValueError(f"{path}.faq_status: unsupported value")
    version_id = match["faq_item_version_id"]
    if version_id is not None and (isinstance(version_id, bool) or not isinstance(version_id, int) or version_id < 1):
        raise ValueError(f"{path}.faq_item_version_id: must be a positive integer or null")
    evidence = match["faq_evidence"]
    if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
        raise ValueError(f"{path}.faq_evidence: must be null or a non-empty string")


def match_pains_independently(result, faq_items, matcher):
    """Apply a separate matcher call to every pain and validate each result.

    ``matcher`` receives one pain and the complete FAQ snapshot. It must return
    only the FAQ fields for that pain; no match is shared between pain rows.
    """
    updated = validate_analysis_result(result)
    for index, pain in enumerate(updated["pains"]):
        match = matcher(pain, faq_items)
        _validate_match(match, f"result.pains[{index}]")
        pain.update(match)
    return validate_analysis_result(updated)
