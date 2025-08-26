# py-basic-agent

A tiny but real agentic system you can run locally. It uses:

* an LLM (via Ollama) for reasoning and tool selection
* pgvector for long‑term memory (document embeddings in Postgres)
* a Rich + prompt\_toolkit REPL for a pleasant CLI
* optional MCP (Model Context Protocol) tools (HTTP façade) for file I/O
* a minimal ETL pipeline (CSV/JSON → transform → save), plus simple web search/fetch tools

> RAG support: The REPL now includes a small Retrieval‑Augmented Generation flow. See [RAG (Retrieval‑Augmented Generation)](#rag-retrievalaugmented-generation) below.

---

## Contents

* [Quick start](#quick-start)
* [Requirements](#requirements)
* [Setup (Linux / macOS / Windows)](#setup-linux--macos--windows)
* [Ollama hosting](#ollama-hosting)
* [Environment variables](#environment-variables)
* [Reset after embedding update](#reset-after-embedding-update)
* [How it works (core concepts)](#how-it-works-core-concepts)
* [Using the REPL](#using-the-repl)
* [ETL mini‑DSL](#etl-mini-dsl)
* [MCP (Model Context Protocol) tools](#mcp-model-context-protocol-tools)
* [RAG (Retrieval‑Augmented Generation)](#rag-retrievalaugmented-generation)
* [Troubleshooting](#troubleshooting)
* [Project layout](#project-layout)

---

## Quick start

> Before you start: make sure Ollama is running and reachable. See **Ollama hosting** for OS‑specific setup (macOS runs Ollama outside Docker; Linux/Windows may use the optional `ollama` service).

```bash
# 1) Start dependencies (DB + optional MCP file server)
docker compose up -d pgvector mcpfs

# 2) Run the agent REPL
docker compose run --rm app
```

In the REPL:

```text
/research Who founded NVIDIA and when?
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3"
/mcp add-http -n fs -u http://host.docker.internal:8765
/mcp tools
/mcp call fs list_files '{"path":"./data"}'
```

Type `/help` for a full list of commands, or `exit()` to quit.

> **RAG quick taste**:
>
> ```
> /rag ingest -p ./knowledge
> /rag show -q "quartz-8127" -k 3
> /rag ask What is the RAG demo code?
> ```

---

## Requirements

You don’t need Python locally. Everything runs in containers.

* Docker Desktop or Engine (compose v2)
* Ollama running on your host (for the LLM + embeddings)

  * pull models you’ll use:

    ```bash
    ollama pull llama3.1:8b
    ollama pull all-minilm
    ```
  * make sure Ollama is reachable from Docker at `http://host.docker.internal:11434`
* \~2–4 GB free RAM for the small models

---

## Setup (Linux / macOS / Windows)

### 1) Clone and prepare `.env`

```bash
git clone <your-repo-url>
cd py-basic-agent
cp .env.example .env
```

Update values as needed (see **Environment variables**).

### 2) Start Postgres (pgvector) and MCP file server

```bash
docker compose up -d pgvector mcpfs
```

> The pgvector container auto‑creates the database, role, and tables via the mounted `db/schema.sql`. No manual DB bootstrapping needed.

### 3) Run the REPL

```bash
docker compose run --rm app
```

### (Optional) Prepare a knowledge folder for RAG

Create `knowledge/` with demo docs; you can use the included `intro.md` and `policies.md` examples.

```bash
mkdir -p knowledge
# these files may already exist in the repo
# echo "# RAG Demo — py-basic-agent" > knowledge/intro.md
# echo "# PII Handling & Redaction Policy (Demo)" > knowledge/policies.md
```

Mount it in compose (see **RAG** section) or use a repo‑relative path with `/rag ingest -p ./knowledge`.

---

## Ollama hosting

* **macOS (recommended)**: run Ollama outside Docker (host app). This is required on macOS so the models can use Metal and to avoid Docker networking issues. Keep `OLLAMA_HOST=http://host.docker.internal:11434`.
* **Linux/Windows (optional in‑container)**: you may run Ollama as a container with Compose. When running the in‑container service, set `OLLAMA_HOST=http://ollama:11434`.

> ⚠️ Experimental: the dockerized Ollama path depends on GPU drivers/permissions. If you hit timeouts or slow responses, prefer running Ollama on the host and use `host.docker.internal:11434`.

---

## Environment variables

All live in `.env`. Defaults are safe for local use.

| Variable            | What it does                                                                                                        | Example                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `OLLAMA_HOST`       | Base URL for Ollama. The app calls `/api/chat` and `/api/embeddings`.                                               | `http://host.docker.internal:11434`                  |
| `AGENT_LLM_MODEL`   | Chat model used by the agent.                                                                                       | `llama3.1:8b`                                        |
| `AGENT_TEMPERATURE` | LLM sampling temperature (float).                                                                                   | `0.2`                                                |
| `AGENT_MAX_STEPS`   | Agent loop max tool steps before giving up.                                                                         | `8`                                                  |
| `AGENT_DB_URL`      | Postgres URL used by `PgVectorMemory`. Provided by compose.                                                         | `postgresql://agent:agentpass@pgvector:5432/agentdb` |
| `AGENT_EMBED_MODEL` | Embedding model name in Ollama.                                                                                     | `all-minilm`                                         |
| `AGENT_EMBED_DIM`   | Embedding vector dimension. Must match `schema.sql`.                                                                | `384`                                                |
| `SERPER_API_KEY`    | API key for the Serper search tool (if enabled).                                                                    | `sk-…`                                               |
| `AGENT_VERBOSE`     | Controls extra logging in some contexts (`true/false`).                                                             | `true`                                               |
| **`KB_PATH`**       | Default folder for RAG ingestion **inside the container**. If you don’t mount it, use `/rag ingest -p ./knowledge`. | `/knowledge`                                         |

### Changing models

* Use a different chat model: set `AGENT_LLM_MODEL` (e.g., `llama3.2:3b`).
* Use a different embedding model: set `AGENT_EMBED_MODEL` and update `AGENT_EMBED_DIM`, then update the DB schema (vector size in `schema.sql`) and recreate DB or migrate:

```sql
ALTER TABLE docs ALTER COLUMN embedding TYPE vector(<new_dim>);
```

---

## Reset after embedding update

If you change `AGENT_EMBED_MODEL` or `AGENT_EMBED_DIM`, ensure the database vector dimension matches the embedding dimension. Easiest path is to recreate the DB volume (auto‑initialized from `db/schema.sql`).

1. Edit `.env` to the new model & dim (defaults use `all-minilm`).
2. Edit `db/schema.sql` to match (e.g., `VECTOR(384)`).
3. Drop and rebuild Postgres (recreates volume and schema):

```bash
docker compose down -v
docker compose up -d pgvector
```

4. (Optional) verify the table shape:

```bash
docker compose exec -T pgvector psql -X -U agent -d agentdb -c "\d+ docs"
```

5. Restart the REPL:

```bash
docker compose run --rm app
```

> Prefer a migration instead of dropping the volume? Use the `ALTER TABLE … TYPE vector(<dim>)` command above.

---

## How it works (core concepts)

### 1) The Agent loop (`agent/core.py`)

* Builds a message list: a system prompt + user task + any observations from tools.
* Calls the LLM with the messages.
* The LLM responds with a JSON tool call or a final answer, e.g.: `{ "tool": "search", "input": {"query": "NVIDIA founders"} }`.
* The agent executes the tool, captures an observation (and often a short LLM summary), appends that back to the conversation, and repeats until a final answer or the step limit is reached.

### 2) The LLM client (`agent/llm.py`)

* Thin async wrapper around Ollama APIs:

  * `/api/chat` for conversation
  * `/api/embeddings` for vectorization
* Normalizes messages and enforces length limits.
* Includes a system prompt that teaches the LLM to return JSON tool calls or a final JSON with a readable summary.

### 3) Tools (`agent/tools.py`)

* Search: Serper (or stub). Returns top results.
* Fetch: Gets a URL, returns `{title, url, text}`.
* ETL: `load_csv/json` → `transform` (select/rename/limit) → `save`.
* Memory: router that calls the configured memory implementation.

Each tool is pure async and returns structured data.

### 4) Memory (`agent/memory/`)

Two implementations (same interface):

* `SimpleMemory` (in‑process, substring search) — great for unit tests.
* `PgVectorMemory` (Postgres + pgvector, cosine similarity):

  * `aupsert(docs)` store content + embeddings
  * `aquery(query, k)` vector search
  * `aadd(text, source, uri, meta)` quick note
  * `adump(n)` latest notes dump (for system prompt context)

Embeddings come from Ollama (`AGENT_EMBED_MODEL`), and the vector size must match DB schema.

### 5) REPL (`agent/repl.py`)

* Rich header + tool hints + keybindings
* History, completion for commands and file paths
* Commands:

  * `/research <question>`
  * `/etl -p <path> -t "<transform>" [-l <out>]`
  * `/etl_from_source -p <url> -t "<transform>" [-l <out>]`
  * `/where <path>`
  * `/mcp …` (see below)
  * `/help`, `exit()`
  * **`/rag …` (see below)**

---

## Using the REPL

Start it:

```bash
docker compose run --rm app
```

Try research:

```text
/research Compare NVIDIA vs AMD GPU market share over the last 2 years.
```

Try ETL:

```text
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3"
```

Check a path:

```text
/where ./data/sales_orders.csv
```

---

## ETL mini‑DSL

Chain operations with semicolons. Works for CSV columns and JSON keys.

* `reorder:colA,colB,colC` — Reorder columns; unspecified columns are appended in original order.
* `rename:old1->new1,old2->new2` — Rename fields/columns. Quote names with spaces or numeric keys: `rename:'1958'->y1958,'unit price'->price`.
* `limit:K` — Truncate rows/objects to K.

Examples:

```text
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3" -l ./data/sales_orders.sample.parquet
```

---

## MCP (Model Context Protocol) tools

MCP lets you add external tools to the agent at runtime (HTTP façade or stdio servers).

Examples:

```text
/mcp add-http -n fs -u http://host.docker.internal:8765
/mcp tools
/mcp call fs list_files '{"path":"./data"}'
```

---

## RAG (Retrieval‑Augmented Generation)

A small, readable RAG layer is built into the REPL so you can demonstrate how articles/notes become ground truth for answers.

### Files & mounting

* Place `.md` and `.txt` files under **`knowledge/`** in your repo.
* Either:

  * run: `/rag ingest -p ./knowledge` (no compose changes), **or**
  * mount the folder into the container and use the default `KB_PATH`:

    ```yaml
    # compose.yml (app service excerpt)
    services:
      app:
        environment:
          KB_PATH: /knowledge
        volumes:
          - ./:/app
          - ./knowledge:/knowledge:ro
    ```

### Commands

```text
/rag ingest [-p PATH] [--glob "*.md,*.txt"]
/rag add -t "text" [-s source] [-u uri]
/rag show -q "query" [-k 6]
/rag ask  <question> [-k 6]
```

* **ingest** — indexes files into pgvector (chunks ≈ 800 words, 150 overlap).
* **add** — stores a one‑off snippet as a doc chunk.
* **show** — vector search only; pretty prints sources/URIs and highlights terms.
* **ask** — retrieves context and asks the LLM to answer **only** from that context (responds “I don’t know.” when not covered).

### Demo knowledge

The repo includes `knowledge/intro.md` and `knowledge/policies.md` with respective test tokens:

* **RAG‑DEMO-INTRO‑CODE:** `quartz-8127`

* **RAG‑PII‑POLICY‑CODE:** `heron-4512`

**PII behavior to demo:**

```text
/rag ask What is Jane Roe's SSN?
# → Per policy, I cannot share that information. [REDACTED]

/rag ask What is the PII policy code?
# → heron-4512
```

### Smoke checks

```text
/rag ingest
/rag show -q "quartz-8127" -k 3
/rag ask What is the RAG demo code?
```

Expect: ingest counts > 0, retrieval shows `intro.md`, answer includes `quartz-8127`.

### Notes

* Embedding dim must match DB schema (`VECTOR(dim)`); change `AGENT_EMBED_MODEL`/`AGENT_EMBED_DIM` together and re‑ingest.
* Apostrophes are safe in `/rag ask` (parser avoids `shlex` pitfalls).
* If `/rag ingest` returns `files=0`, verify mounts:

  * `/where /knowledge` vs `/where ./knowledge`.

---

## Troubleshooting

* **Ollama not reachable**: check `OLLAMA_HOST`; test with `curl $OLLAMA_HOST/api/tags` from inside a container.
* **No embeddings / zero scores**: ensure the embed model is pulled (`all-minilm`) and dims match schema.
* **RAG ingest finds zero files**: mount `./knowledge:/knowledge:ro` or pass `-p ./knowledge`.
* **Docker networking**: on macOS/Windows use `host.docker.internal`; on Linux, see `extra_hosts` in compose.

---

## Project layout

```
app/
  agent/
    core.py         # agent loop
    llm.py          # Ollama chat/embeddings client
    repl.py         # REPL (commands include research/etl/mcp/rag)
    tools.py        # tool registry (search/fetch/etl/memory)
    memory/
      __init__.py   # get_memory() factory
      pg_store.py   # PgVectorMemory impl
      simple.py     # SimpleMemory impl
knowledge/
  intro.md          # RAG demo document (token quartz-8127)
  policies.md       # optional PII policy demo (token heron-4512)
compose.yml
.env.example
README.md
```

