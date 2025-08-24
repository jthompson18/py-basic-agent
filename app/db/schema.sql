-- Runs once when the data volume is created.
-- If you change this, you must `docker compose down -v` to re-run it.

CREATE EXTENSION IF NOT EXISTS vector;

-- Memory table used by the agent’s PgVectorMemory
CREATE TABLE IF NOT EXISTS docs (
  id        BIGSERIAL PRIMARY KEY,
  source    TEXT NOT NULL,
  uri       TEXT NOT NULL,
  meta      JSONB,
  content   TEXT NOT NULL,
  embedding VECTOR(768),               -- match AGENT_EMBED_DIM (nomic-embed-text = 768)
  UNIQUE (source, uri)
);

-- ANN index (cosine). <=> returns cosine distance with this opclass.
-- We compute similarity as (1 - distance) in code.
CREATE INDEX IF NOT EXISTS idx_docs_embedding_ivfflat_cos
  ON docs USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
