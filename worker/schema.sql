-- nst notices (Cloudflare D1)
CREATE TABLE IF NOT EXISTS notices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  issuer TEXT NOT NULL,
  prefecture TEXT,
  level TEXT,
  notice_type TEXT,
  published_date TEXT,
  deadline_date TEXT,
  bid_date TEXT,
  source_url TEXT NOT NULL,
  attachment_url TEXT,
  framework TEXT,
  collected_at TEXT NOT NULL,
  UNIQUE(title, issuer, source_url)
);
CREATE INDEX IF NOT EXISTS idx_notices_date ON notices(published_date);
CREATE INDEX IF NOT EXISTS idx_notices_pref ON notices(prefecture);
