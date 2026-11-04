-- FAQ Анкетки: stable items and immutable observed versions.

CREATE TABLE IF NOT EXISTS faq_items (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id         BIGINT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    external_key      TEXT NOT NULL,
    question          TEXT NOT NULL,
    url               TEXT NOT NULL,
    first_seen_at     TIMESTAMPTZ NOT NULL,
    last_seen_at      TIMESTAMPTZ NOT NULL,
    first_seen_run_id BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    last_seen_run_id  BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (source_id, external_key)
);
CREATE INDEX IF NOT EXISTS faq_items_source_idx ON faq_items (source_id);

CREATE TABLE IF NOT EXISTS faq_item_versions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    faq_item_id       BIGINT NOT NULL REFERENCES faq_items(id) ON DELETE RESTRICT,
    version_no        INTEGER NOT NULL CHECK (version_no > 0),
    run_id            BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE RESTRICT,
    observed_at       TIMESTAMPTZ NOT NULL,
    question          TEXT NOT NULL,
    answer            TEXT NOT NULL,
    source_updated_at TIMESTAMPTZ,
    content_hash      TEXT NOT NULL,
    raw_payload       JSONB NOT NULL,
    UNIQUE (faq_item_id, version_no)
);
CREATE INDEX IF NOT EXISTS faq_item_versions_item_idx
    ON faq_item_versions (faq_item_id, version_no DESC);
CREATE INDEX IF NOT EXISTS faq_item_versions_observed_idx
    ON faq_item_versions (observed_at);

INSERT INTO sources (project_id, platform, external_key, name, url)
SELECT id, 'faq', 'anketka.faq', 'FAQ Анкетки',
       'https://www.anketka.ru/faq'
FROM projects WHERE code = 'anketka'
ON CONFLICT (project_id, platform, external_key) DO NOTHING;
