"""Provider-neutral structured analytics for RuStore review versions."""

from .client import AnalyticsClientError, StructuredAnalyticsClient
from .domain import aggregate_pains, candidate_pain_key
from .faq import match_pains_independently
from .persistence import persist_completed_run
from .schema import ANALYSIS_RESULT_SCHEMA, AnalysisValidationError, validate_analysis_result
from .selection import (
    select_faq_items,
    select_incremental_reviews,
    select_latest_faq_run,
    select_retrospective_reviews,
)

__all__ = [
    "ANALYSIS_RESULT_SCHEMA",
    "AnalysisValidationError",
    "AnalyticsClientError",
    "StructuredAnalyticsClient",
    "aggregate_pains",
    "candidate_pain_key",
    "match_pains_independently",
    "persist_completed_run",
    "select_faq_items",
    "select_incremental_reviews",
    "select_latest_faq_run",
    "select_retrospective_reviews",
    "validate_analysis_result",
]
