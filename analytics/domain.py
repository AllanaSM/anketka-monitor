"""Pure domain operations for review analytics results."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from .schema import validate_analysis_result


def candidate_pain_key(candidate):
    """Return a stable key for a candidate kept in the shared dictionary."""
    if not isinstance(candidate, dict):
        raise ValueError("Candidate pain must be an object")
    basis = {
        "area_key": candidate["area_key"],
        "label": candidate["label"],
        "description": candidate["description"],
    }
    digest = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"candidate_{digest[:24]}"


def pain_identity(pain):
    """Return the dictionary key for an existing or candidate pain."""
    return pain["pain_key"] or candidate_pain_key(pain["candidate"])


def _selected_versions(items):
    selected = {}
    for item in items:
        review_id = item["review_id"]
        current = selected.get(review_id)
        if current is None or item["version_no"] > current["version_no"]:
            selected[review_id] = item
        elif item["version_no"] == current["version_no"] and item["review_version_id"] != current["review_version_id"]:
            raise ValueError(f"Duplicate review version number for review {review_id}")
    return selected


def aggregate_pains(items):
    """Aggregate pains once per unique review, using its latest supplied version."""
    selected = _selected_versions(items)
    aggregates = {}
    for item in selected.values():
        result = validate_analysis_result(item["result"])
        seen_for_review = set()
        for pain in result["pains"]:
            key = pain_identity(pain)
            if key in seen_for_review:
                continue
            seen_for_review.add(key)
            aggregate = aggregates.setdefault(key, {
                "pain_key": key,
                "label": (pain["candidate"] or {}).get("label") or key,
                "area_key": (pain["candidate"] or {}).get("area_key"),
                "area_label": (pain["candidate"] or {}).get("area_label"),
                "description": (pain["candidate"] or {}).get("description"),
                "pain_review_count": 0,
                "analyzed_review_count": len(selected),
                "share_percent": 0.0,
                "intensity_counts": Counter(),
                "tone_counts": Counter(),
                "faq_status_counts": Counter(),
                "examples": [],
            })
            aggregate["pain_review_count"] += 1
            aggregate["intensity_counts"][pain["intensity"]] += 1
            aggregate["tone_counts"][result["tone"]] += 1
            aggregate["faq_status_counts"][pain["faq_status"]] += 1
            if len(aggregate["examples"]) < 3:
                aggregate["examples"].append({
                    "review_id": item["review_id"],
                    "review_version_id": item["review_version_id"],
                    "text": item.get("text", ""),
                })
    for aggregate in aggregates.values():
        aggregate["share_percent"] = round(
            aggregate["pain_review_count"] / aggregate["analyzed_review_count"] * 100, 2
        ) if aggregate["analyzed_review_count"] else 0.0
        for key in ("intensity_counts", "tone_counts", "faq_status_counts"):
            aggregate[key] = dict(aggregate[key])
    return aggregates
