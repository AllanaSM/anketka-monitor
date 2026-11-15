-- Аналитика отзывов v1: неизменяемые запуски, словарь болей и результаты анализа.
-- Неуспешные попытки не записываются в эти таблицы: analysis_runs хранит только complete.

CREATE TABLE IF NOT EXISTS analysis_runs (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_key                 UUID NOT NULL UNIQUE,
    project_id              BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    mode                    TEXT NOT NULL CHECK (mode IN ('incremental', 'retrospective')),
    source_collection_run_id BIGINT REFERENCES collection_runs(id) ON DELETE RESTRICT,
    faq_collection_run_id   BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    requested_by_chat_id    BIGINT,
    status                  TEXT NOT NULL DEFAULT 'complete' CHECK (status = 'complete'),
    model_provider          TEXT NOT NULL,
    model_name              TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_count             INTEGER NOT NULL DEFAULT 0 CHECK (input_count >= 0),
    result_count            INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
    error_count             INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    diagnostics             JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at >= started_at),
    CHECK (
        (mode = 'incremental' AND source_collection_run_id IS NOT NULL)
        OR (mode = 'retrospective' AND source_collection_run_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS analysis_runs_project_finished_idx
    ON analysis_runs (project_id, finished_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS analysis_runs_incremental_source_unique
    ON analysis_runs (source_collection_run_id)
    WHERE mode = 'incremental';

CREATE TABLE IF NOT EXISTS pain_dictionary (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id          BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    pain_key            TEXT NOT NULL,
    area_key            TEXT NOT NULL,
    area_label          TEXT NOT NULL,
    label               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL CHECK (status IN ('approved', 'candidate', 'rejected')),
    created_by_chat_id  BIGINT,
    approved_by_chat_id BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at         TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (project_id, pain_key),
    CHECK (
        status <> 'approved'
        OR (approved_by_chat_id IS NOT NULL AND approved_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS pain_dictionary_project_status_idx
    ON pain_dictionary (project_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS pain_dictionary_area_idx
    ON pain_dictionary (project_id, area_key, status);

CREATE TABLE IF NOT EXISTS review_analysis (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    analysis_run_id   BIGINT NOT NULL REFERENCES analysis_runs(id) ON DELETE RESTRICT,
    review_version_id BIGINT NOT NULL REFERENCES review_versions(id) ON DELETE RESTRICT,
    tone              TEXT NOT NULL CHECK (tone IN ('positive', 'mixed', 'neutral', 'negative')),
    summary           TEXT NOT NULL,
    confidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
    needs_review      BOOLEAN NOT NULL DEFAULT FALSE,
    raw_result        JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (analysis_run_id, review_version_id)
);

CREATE INDEX IF NOT EXISTS review_analysis_run_idx
    ON review_analysis (analysis_run_id, id);
CREATE INDEX IF NOT EXISTS review_analysis_version_idx
    ON review_analysis (review_version_id);

CREATE TABLE IF NOT EXISTS review_analysis_pains (
    review_analysis_id  BIGINT NOT NULL REFERENCES review_analysis(id) ON DELETE RESTRICT,
    pain_id             BIGINT NOT NULL REFERENCES pain_dictionary(id) ON DELETE RESTRICT,
    intensity           TEXT NOT NULL CHECK (intensity IN ('low', 'medium', 'high')),
    pain_evidence       TEXT NOT NULL,
    confidence          JSONB NOT NULL DEFAULT '{}'::jsonb,
    faq_status          TEXT NOT NULL CHECK (faq_status IN ('answered', 'partially_answered', 'not_answered', 'undetermined')),
    faq_item_version_id BIGINT REFERENCES faq_item_versions(id) ON DELETE RESTRICT,
    faq_evidence        TEXT,
    PRIMARY KEY (review_analysis_id, pain_id)
);

CREATE INDEX IF NOT EXISTS review_analysis_pains_pain_idx
    ON review_analysis_pains (pain_id, review_analysis_id);
CREATE INDEX IF NOT EXISTS review_analysis_pains_faq_idx
    ON review_analysis_pains (faq_item_version_id)
    WHERE faq_item_version_id IS NOT NULL;
