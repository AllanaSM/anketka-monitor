-- Анкетка monitor: initial PostgreSQL schema.
-- This migration intentionally contains no AI tables or AI seed data.

CREATE TABLE IF NOT EXISTS projects (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    history_from DATE NOT NULL,
    timezone     TEXT NOT NULL DEFAULT 'Europe/Moscow',
    report_time  TIME NOT NULL DEFAULT '09:00:00',
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sources (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    platform     TEXT NOT NULL,
    external_key TEXT NOT NULL,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    settings     JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (project_id, platform, external_key)
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id         BIGINT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    run_key           UUID NOT NULL UNIQUE,
    mode              TEXT NOT NULL CHECK (mode IN ('backfill', 'incremental')),
    status            TEXT NOT NULL CHECK (status IN ('running', 'complete', 'partial', 'failed')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    requested_from    DATE NOT NULL,
    counters          JSONB NOT NULL DEFAULT '{}'::jsonb,
    diagnostics       JSONB NOT NULL DEFAULT '[]'::jsonb,
    snapshot_path     TEXT,
    collector_version TEXT NOT NULL,
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'running' AND finished_at IS NULL) OR status <> 'running')
);

CREATE UNIQUE INDEX IF NOT EXISTS collection_runs_one_running_source
    ON collection_runs (source_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS collection_runs_source_started_idx
    ON collection_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id            BIGINT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    external_id          TEXT NOT NULL,
    url                  TEXT NOT NULL,
    url_kind             TEXT NOT NULL CHECK (url_kind IN ('list', 'direct')),
    first_seen_at        TIMESTAMPTZ NOT NULL,
    last_seen_at         TIMESTAMPTZ NOT NULL,
    first_seen_run_id    BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    last_seen_run_id     BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    likes_count          INTEGER CHECK (likes_count IS NULL OR likes_count >= 0),
    dislikes_count       INTEGER CHECK (dislikes_count IS NULL OR dislikes_count >= 0),
    replies_count        INTEGER CHECK (replies_count IS NULL OR replies_count >= 0),
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS reviews_source_idx ON reviews (source_id);

CREATE TABLE IF NOT EXISTS review_versions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id         BIGINT NOT NULL REFERENCES reviews(id) ON DELETE RESTRICT,
    version_no        INTEGER NOT NULL CHECK (version_no > 0),
    run_id            BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    observed_at       TIMESTAMPTZ NOT NULL,
    author_name       TEXT,
    text              TEXT NOT NULL,
    rating            SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    published_at      TIMESTAMPTZ,
    source_edited_at  TIMESTAMPTZ,
    content_hash      TEXT NOT NULL,
    raw_payload       JSONB NOT NULL,
    UNIQUE (review_id, version_no)
);
CREATE INDEX IF NOT EXISTS review_versions_review_idx
    ON review_versions (review_id, version_no DESC);
CREATE INDEX IF NOT EXISTS review_versions_published_idx
    ON review_versions (published_at);

CREATE TABLE IF NOT EXISTS replies (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id      BIGINT NOT NULL REFERENCES reviews(id) ON DELETE RESTRICT,
    external_key   TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('user', 'developer', 'unknown')),
    first_seen_at  TIMESTAMPTZ NOT NULL,
    last_seen_at   TIMESTAMPTZ NOT NULL,
    likes_count    INTEGER CHECK (likes_count IS NULL OR likes_count >= 0),
    dislikes_count INTEGER CHECK (dislikes_count IS NULL OR dislikes_count >= 0),
    UNIQUE (review_id, external_key)
);
CREATE INDEX IF NOT EXISTS replies_review_idx ON replies (review_id);

CREATE TABLE IF NOT EXISTS reply_versions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reply_id          BIGINT NOT NULL REFERENCES replies(id) ON DELETE RESTRICT,
    version_no        INTEGER NOT NULL CHECK (version_no > 0),
    run_id            BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    observed_at       TIMESTAMPTZ NOT NULL,
    author_name       TEXT,
    author_external_id TEXT,
    text              TEXT NOT NULL,
    published_at      TIMESTAMPTZ,
    source_edited_at  TIMESTAMPTZ,
    content_hash      TEXT NOT NULL,
    raw_payload       JSONB NOT NULL,
    UNIQUE (reply_id, version_no)
);
CREATE INDEX IF NOT EXISTS reply_versions_reply_idx
    ON reply_versions (reply_id, version_no DESC);

CREATE TABLE IF NOT EXISTS change_events (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    review_version_id BIGINT REFERENCES review_versions(id) ON DELETE RESTRICT,
    reply_version_id  BIGINT REFERENCES reply_versions(id) ON DELETE RESTRICT,
    event_type        TEXT NOT NULL CHECK (event_type IN ('review_new', 'review_updated', 'reply_new', 'reply_updated')),
    origin            TEXT NOT NULL CHECK (origin IN ('backfill', 'monitoring', 'late_discovery')),
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((review_version_id IS NULL) <> (reply_version_id IS NULL)),
    UNIQUE (review_version_id),
    UNIQUE (reply_version_id)
);
CREATE INDEX IF NOT EXISTS change_events_detected_idx ON change_events (detected_at, id);

CREATE TABLE IF NOT EXISTS telegram_subscriptions (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id     BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    chat_id        BIGINT NOT NULL,
    display_name   TEXT,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    subscribed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, chat_id)
);
CREATE INDEX IF NOT EXISTS telegram_subscriptions_active_idx
    ON telegram_subscriptions (project_id, enabled);

CREATE TABLE IF NOT EXISTS reports (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    report_key    TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('initial', 'daily', 'recovery')),
    status        TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed')),
    period_from   TIMESTAMPTZ NOT NULL,
    period_to     TIMESTAMPTZ NOT NULL,
    scheduled_at  TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    prepared_at   TIMESTAMPTZ,
    coverage      JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (period_to > period_from),
    UNIQUE (project_id, report_key)
);
CREATE INDEX IF NOT EXISTS reports_project_scheduled_idx
    ON reports (project_id, scheduled_at);

CREATE TABLE IF NOT EXISTS report_items (
    report_id BIGINT NOT NULL REFERENCES reports(id) ON DELETE RESTRICT,
    event_id  BIGINT NOT NULL REFERENCES change_events(id) ON DELETE RESTRICT,
    position  INTEGER NOT NULL CHECK (position > 0),
    PRIMARY KEY (report_id, event_id),
    UNIQUE (report_id, position),
    UNIQUE (event_id)
);

CREATE TABLE IF NOT EXISTS telegram_deliveries (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_id            BIGINT NOT NULL REFERENCES reports(id) ON DELETE RESTRICT,
    subscription_id      BIGINT NOT NULL REFERENCES telegram_subscriptions(id) ON DELETE RESTRICT,
    part_no               INTEGER NOT NULL CHECK (part_no > 0),
    payload               TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'retry', 'uncertain', 'failed')),
    attempts              INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at       TIMESTAMPTZ,
    locked_until          TIMESTAMPTZ,
    telegram_message_id   BIGINT,
    sent_at               TIMESTAMPTZ,
    last_error            TEXT,
    UNIQUE (report_id, subscription_id, part_no),
    CHECK (status <> 'sent' OR (telegram_message_id IS NOT NULL AND sent_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS telegram_deliveries_queue_idx
    ON telegram_deliveries (status, next_attempt_at);

INSERT INTO projects (code, name, history_from, timezone, report_time)
VALUES ('anketka', 'Анкетка', DATE '2026-01-01', 'Europe/Moscow', TIME '09:00:00')
ON CONFLICT (code) DO NOTHING;

INSERT INTO sources (project_id, platform, external_key, name, url)
SELECT id, 'rustore', 'ru.anketka', 'RuStore',
       'https://www.rustore.ru/catalog/app/ru.anketka/reviews'
FROM projects WHERE code = 'anketka'
ON CONFLICT (project_id, platform, external_key) DO NOTHING;
