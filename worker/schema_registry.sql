-- nst registry assets (Cloudflare D1 / SQLite)
-- Companion to schema.sql (which holds the collected `notices` table).
-- One table per registry CSV in data/. Column names mirror the CSV headers
-- (snake_case) so scripts/import_registry_to_d1.py can map them 1:1.
-- Conventions:
--   * Boolean flags are INTEGER 0/1 (NULL = not recorded / empty in CSV).
--   * Empty CSV fields import as NULL.
--   * Tables whose CSV has no reliable natural unique key use a surrogate
--     `id INTEGER PRIMARY KEY AUTOINCREMENT`; uniqueness that the data does
--     guarantee is enforced with UNIQUE constraints noted per table.

-- data/master_source_list.csv -- master registry of issuing bodies.
-- No UNIQUE on (org_name, prefecture, official_domain): the CSV carries a
-- small number of pre-existing duplicate keys, kept as-is.
-- in_kkj / in_dir2_top are INTEGER 0/1: 'yes' -> 1, 'no' -> 0, '' -> NULL.
-- in_kkj in the CSV also has qualified values such as 'no (...)'; those map
-- to 0 and the original text is preserved in in_kkj_raw (NULL otherwise).
CREATE TABLE IF NOT EXISTS master_source_list (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prefecture TEXT,
  level TEXT,
  org_name TEXT NOT NULL,
  group_ministry TEXT,
  source_url_kkj TEXT,
  official_domain TEXT,
  framework_guess_by_url TEXT,
  in_kkj INTEGER,
  in_kkj_raw TEXT,
  in_dir2_top INTEGER,
  verification_status TEXT,
  daily_collection_target TEXT,
  source_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_master_domain ON master_source_list(official_domain);
CREATE INDEX IF NOT EXISTS idx_master_pref ON master_source_list(prefecture);
CREATE INDEX IF NOT EXISTS idx_master_status ON master_source_list(verification_status);

-- data/objects_registry.csv -- one row per registered object.
-- object_id (OBJ-K.... / OBJ-D....) is unique in the CSV.
CREATE TABLE IF NOT EXISTS objects_registry (
  object_id TEXT PRIMARY KEY,
  org_name TEXT NOT NULL,
  prefecture TEXT,
  level TEXT,
  official_domain TEXT,
  candidate_endpoint_url TEXT,
  framework TEXT,
  source TEXT
);
CREATE INDEX IF NOT EXISTS idx_objects_domain ON objects_registry(official_domain);
CREATE INDEX IF NOT EXISTS idx_objects_pref ON objects_registry(prefecture);

-- data/endpoints.csv -- polling endpoints, grouped by URL.
-- endpoint_id (EP-....) is the primary key; url is unique in the CSV.
-- status: 'candidate' = pollable, 'disabled' = do-not-poll.
CREATE TABLE IF NOT EXISTS endpoints (
  endpoint_id TEXT PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  host TEXT,
  object_count INTEGER,
  object_ids TEXT,
  poll_tier TEXT,
  terms_status TEXT,
  status TEXT
);
CREATE INDEX IF NOT EXISTS idx_endpoints_host ON endpoints(host);
CREATE INDEX IF NOT EXISTS idx_endpoints_status ON endpoints(status);

-- data/object_lines.csv -- per-object announcement lines (business lines
-- sharing one endpoint host page). (object_id, line_url) is unique in the CSV.
CREATE TABLE IF NOT EXISTS object_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT NOT NULL,
  org_name TEXT,
  line_label TEXT,
  line_url TEXT,
  note TEXT,
  UNIQUE(object_id, line_url)
);
CREATE INDEX IF NOT EXISTS idx_object_lines_object ON object_lines(object_id);

-- data/site_templates.csv -- sampled page-structure classification (P1-P7).
-- No natural unique key in the CSV (an org can have several sampled pages),
-- so a surrogate id is used.
CREATE TABLE IF NOT EXISTS site_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_name TEXT NOT NULL,
  domain TEXT,
  list_url TEXT,
  p_class TEXT,
  list_structure TEXT,
  detail_pattern TEXT,
  pagination TEXT,
  detail_format TEXT,
  evidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_templates_domain ON site_templates(domain);
CREATE INDEX IF NOT EXISTS idx_templates_pclass ON site_templates(p_class);

-- data/dir2_objects.csv -- objects enumerated once from the second directory
-- (neutral code name "dir2"). Populated after reconciliation; may be empty.
CREATE TABLE IF NOT EXISTS dir2_objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_name TEXT NOT NULL,
  prefecture TEXT,
  official_url TEXT,
  source_page TEXT
);
CREATE INDEX IF NOT EXISTS idx_dir2_pref ON dir2_objects(prefecture);

-- data/open_items.csv -- unresolved objects awaiting follow-up.
-- bucket: conn_error / manual_unresolved / robots_denied / manual_confirm /
-- unverified / reverify.
CREATE TABLE IF NOT EXISTS open_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_name TEXT NOT NULL,
  prefecture TEXT,
  level TEXT,
  official_domain TEXT,
  source_url TEXT,
  bucket TEXT,
  status TEXT,
  next_action TEXT
);
CREATE INDEX IF NOT EXISTS idx_open_items_bucket ON open_items(bucket);
CREATE INDEX IF NOT EXISTS idx_open_items_domain ON open_items(official_domain);

-- data/candidates_joint_bodies.csv -- candidate joint bodies (single-purpose
-- associations / wide-area unions) from official code lists, pending
-- per-body verification before intake. `code` is TEXT (leading zeros and
-- official list codes must be preserved verbatim).
CREATE TABLE IF NOT EXISTS candidates_joint_bodies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_name TEXT NOT NULL,
  prefecture TEXT,
  code TEXT,
  official_url_from_list TEXT,
  source_list TEXT,
  note TEXT,
  verification_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_pref ON candidates_joint_bodies(prefecture);
CREATE INDEX IF NOT EXISTS idx_candidates_code ON candidates_joint_bodies(code);
