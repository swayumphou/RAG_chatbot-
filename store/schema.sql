-- RUPP-SPKB v2.0 metadata store (implementation/spec/INTERFACES.md §3).
--
-- Applied by store/db.py on every open. IF NOT EXISTS keeps re-application
-- idempotent; the column set is exactly the INTERFACES contract.
--
-- tech_stack and keywords are JSON arrays. created_at / updated_at are
-- ISO-8601 UTC strings. Contact fields live here only: they are never copied
-- into Qdrant payloads, LLM prompts, or logs.

CREATE TABLE IF NOT EXISTS senior (
  senior_id              TEXT PRIMARY KEY,
  full_name_latin        TEXT NOT NULL,
  full_name_khmer        TEXT,
  name_normalized        TEXT NOT NULL,
  program                TEXT NOT NULL,
  cohort_year            INTEGER NOT NULL,
  supervisor_id          TEXT,
  contact_email          TEXT,
  github                 TEXT,
  linkedin               TEXT,
  website                TEXT,
  consent_public_contact INTEGER NOT NULL DEFAULT 0,
  consent_relay          INTEGER NOT NULL DEFAULT 0,
  telegram_user_hash     TEXT,
  status                 TEXT NOT NULL DEFAULT 'active',
  created_at             TEXT NOT NULL,
  updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS senior_alias (
  senior_id        TEXT NOT NULL REFERENCES senior(senior_id),
  alias_normalized TEXT NOT NULL,
  script           TEXT NOT NULL,
  PRIMARY KEY (senior_id, alias_normalized)
);
CREATE INDEX IF NOT EXISTS idx_alias ON senior_alias(alias_normalized);

CREATE TABLE IF NOT EXISTS project (
  project_id    TEXT PRIMARY KEY,
  title_en      TEXT NOT NULL,
  title_khmer   TEXT,
  abstract      TEXT NOT NULL,
  year          INTEGER NOT NULL,
  term          TEXT,
  program       TEXT NOT NULL,
  domain        TEXT,
  tech_stack    TEXT,
  keywords      TEXT,
  repo_url      TEXT,
  demo_url      TEXT,
  supervisor_id TEXT,
  permission    TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authorship (
  project_id TEXT NOT NULL REFERENCES project(project_id),
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  role       TEXT,
  PRIMARY KEY (project_id, senior_id)
);

CREATE TABLE IF NOT EXISTS document (
  doc_id       TEXT PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(project_id),
  doc_type     TEXT NOT NULL,
  file_path    TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  page_count   INTEGER,
  parser       TEXT,
  parse_status TEXT NOT NULL,
  chunk_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS query_log (
  query_id         TEXT PRIMARY KEY,
  surface          TEXT NOT NULL DEFAULT 'telegram',
  tg_user_hash     TEXT,
  tg_message_id    INTEGER,
  raw_query        TEXT NOT NULL,
  normalized_query TEXT NOT NULL,
  intent           TEXT NOT NULL,
  resolved_senior  TEXT,
  retrieved_json   TEXT NOT NULL,
  clicked_project  TEXT,
  rating           INTEGER,
  latency_ms       INTEGER,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qlog_time ON query_log(created_at);

CREATE TABLE IF NOT EXISTS relay_message (
  relay_id   TEXT PRIMARY KEY,
  senior_id  TEXT NOT NULL REFERENCES senior(senior_id),
  from_hash  TEXT NOT NULL,
  body       TEXT NOT NULL,
  status     TEXT NOT NULL,
  created_at TEXT NOT NULL
);
