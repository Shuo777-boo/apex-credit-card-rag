CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS documents (
  doc_id text PRIMARY KEY, title text NOT NULL, category text NOT NULL,
  card_tier text NOT NULL, effective_year integer NOT NULL, content text NOT NULL,
  content_hash text NOT NULL, policy_code text, dataset_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_cache (
  fingerprint text NOT NULL, text_hash text NOT NULL,
  dimension integer NOT NULL, embedding vector NOT NULL,
  PRIMARY KEY(fingerprint, text_hash)
);
CREATE TABLE IF NOT EXISTS index_builds (
  build_id text PRIMARY KEY, table_name text UNIQUE NOT NULL, dataset_hash text NOT NULL,
  config jsonb NOT NULL, status text NOT NULL DEFAULT 'importing',
  expected_chunks integer NOT NULL, document_count integer NOT NULL,
  error_code text, stats jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS active_build (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  build_id text NOT NULL REFERENCES index_builds(build_id)
);
CREATE TABLE IF NOT EXISTS evaluation_runs (
  run_id text PRIMARY KEY, status text NOT NULL, config jsonb NOT NULL,
  metrics jsonb NOT NULL DEFAULT '{}', artifacts jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS evaluation_results (
  run_id text NOT NULL REFERENCES evaluation_runs(run_id), query_id text NOT NULL,
  result jsonb NOT NULL, PRIMARY KEY(run_id,query_id)
);
