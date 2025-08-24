CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS docs (
  id        BIGSERIAL PRIMARY KEY,
  source    TEXT NOT NULL,
  uri       TEXT NOT NULL,
  meta      JSONB,
  content   TEXT NOT NULL,
  embedding VECTOR(768),
  UNIQUE (source, uri)
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public' AND indexname = 'idx_docs_embedding_cosine'
  ) THEN
    CREATE INDEX idx_docs_embedding_cosine
      ON docs USING ivfflat (embedding vector_cosine_ops)
      WITH (lists = 100);
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO agent;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE docs TO agent;
