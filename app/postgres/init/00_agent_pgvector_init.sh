#!/usr/bin/env bash
# postgres/init/00_agent_pgvector_init.sh
# Initializes the pgvector-backed memory schema for the agent.

set -euo pipefail

# You can override these at runtime via environment variables
: "${EMBED_DIM:=768}"                                   # must match your embedding model dimension
: "${POSTGRES_DB:=${PGDATABASE:-agentdb}}"
: "${POSTGRES_USER:=${PGUSER:-agent}}"

echo ">> Initializing pgvector schema in database: ${POSTGRES_DB} (dim=${EMBED_DIM})"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  -- Enable pgvector
  CREATE EXTENSION IF NOT EXISTS vector;

  -- Main memory table
  CREATE TABLE IF NOT EXISTS docs (
    id        BIGSERIAL PRIMARY KEY,
    source    TEXT,           -- e.g., "web", "etl", "note"
    uri       TEXT,           -- canonical identifier/URL if any
    meta      JSONB,          -- arbitrary metadata (title, author, etc.)
    content   TEXT,           -- raw or cleaned text chunk
    embedding VECTOR(${EMBED_DIM})
  );

  -- Useful indexes
  CREATE INDEX IF NOT EXISTS docs_uri_idx ON docs (uri);
  -- Cosine ANN index (tune 'lists' per data size)
  CREATE INDEX IF NOT EXISTS docs_embedding_idx
    ON docs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
SQL

echo ">> pgvector memory schema ready."
