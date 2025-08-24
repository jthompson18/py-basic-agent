# py-basic-agent

A tiny but real agentic system you can run locally. It uses:

* an LLM (via **Ollama**) for reasoning and tool selection
* **pgvector** for long-term memory (document embeddings in Postgres)
* a **Rich** + **prompt\_toolkit** REPL for a pleasant CLI
* optional **MCP** (Model Context Protocol) tools (HTTP façade) for file I/O
* a minimal **ETL** pipeline (CSV/JSON → transform → save), plus simple web **search/fetch** tools

This repo is intentionally small and readable—ideal for learning how agent loops, tool calls, memory, and LLMs work together.

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
* [ETL mini-DSL](#etl-mini-dsl)
* [MCP (Model Context Protocol) tools](#mcp-model-context-protocol-tools)
* [Troubleshooting](#troubleshooting)
* [Project layout](#project-layout)

---

## Quick start

> Before you start: make sure **Ollama is running and reachable**. See [Ollama hosting](#ollama-hosting) for OS-specific setup (macOS runs Ollama outside Docker; Linux/Windows may use the optional `ollama` service).

```bash
# 1) Start dependencies (DB + optional MCP file server)
docker compose up -d pgvector mcpfs

# 2) Run the agent REPL
docker compose run --rm app
```

In the REPL:

```
/research Who founded NVIDIA and when?
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3"
/mcp add-http -n fs -u http://host.docker.internal:8765
/mcp tools
/mcp call fs list_files '{"path":"./data"}'
```

Type `/help` for a full list of commands, or `exit()` to quit.

---

## Requirements

You don’t need Python locally. Everything runs in containers.

* **Docker** Desktop or Engine (compose v2)
* **Ollama** running on your host (for the LLM + embeddings)

  * [https://ollama.com/](https://ollama.com/)
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

Update values as needed (see [Environment variables](#environment-variables)).

### 2) Start Postgres (pgvector) and MCP file server

```bash
docker compose up -d pgvector mcpfs
```

> The pgvector container auto-creates the database, role, and tables via the mounted `db/schema.sql`. No manual DB bootstrapping needed.

### 3) Run the REPL

```bash
docker compose run --rm app
```

### OS Notes

* **macOS**: Works out of the box. Docker Desktop provides `host.docker.internal`.
* **Windows**:

  * Use **Powershell**. Line-continuations use backtick `` ` `` instead of `\`.
  * Ensure Docker Desktop is running.
* **Linux**:

  * We already map `host.docker.internal` using Compose `extra_hosts`. If you use a non-Docker network, ensure your host’s Ollama is reachable (or set `OLLAMA_HOST` to a LAN IP).

---

## Ollama hosting

**macOS (recommended):** run Ollama **outside** Docker (host app). This is required on macOS so the models can use Metal and to avoid Docker networking issues. Keep `OLLAMA_HOST=http://host.docker.internal:11434`.

**Linux/Windows (optional in-container):** you may run Ollama **as a container** with Compose. Open `docker-compose.yml` and **uncomment** the `ollama:` service block, then start it:

```bash
docker compose up -d ollama
# (and usually alongside other deps)
docker compose up -d pgvector mcpfs ollama
```

When running the in-container service, set in your `.env`:

```
OLLAMA_HOST=http://ollama:11434
```

> ⚠️ **Experimental**: the dockerized Ollama path is not 100% tested here and may be flaky depending on GPU drivers and container permissions. If you hit timeouts or slow responses, prefer running Ollama on the host and keep `OLLAMA_HOST` pointed at `host.docker.internal:11434`.

## Environment variables

All live in `.env`. Defaults are safe for local use.

| Variable            | What it does                                                          | Example                                              |
| ------------------- | --------------------------------------------------------------------- | ---------------------------------------------------- |
| `OLLAMA_HOST`       | Base URL for Ollama. The app calls `/api/chat` and `/api/embeddings`. | `http://host.docker.internal:11434`                  |
| `AGENT_LLM_MODEL`   | Chat model used by the agent.                                         | `llama3.1:8b`                                        |
| `AGENT_TEMPERATURE` | LLM sampling temperature (float).                                     | `0.2`                                                |
| `AGENT_MAX_STEPS`   | Agent loop max tool steps before giving up.                           | `8`                                                  |
| `AGENT_DB_URL`      | Postgres URL used by `PgVectorMemory`. Provided by compose.           | `postgresql://agent:agentpass@pgvector:5432/agentdb` |
| `AGENT_EMBED_MODEL` | Embedding model name in Ollama.                                       | `all-minilm`                                         |
| `AGENT_EMBED_DIM`   | Embedding vector dimension. **Must match schema.sql**.                | `384`                                                |
| `SERPER_API_KEY`    | API key for the Serper search tool (if enabled).                      | `sk-...`                                             |
| `AGENT_VERBOSE`     | Controls extra logging in some contexts (`true/false`).               | `true`                                               |

### Changing models

* Use a different chat model: set `AGENT_LLM_MODEL` (e.g., `llama3.2:3b`).
* Use a different embedding model:

  * set `AGENT_EMBED_MODEL` **and**
  * update `AGENT_EMBED_DIM`
  * update the DB schema (vector size in `schema.sql`) and recreate DB **or** migrate:

    ```sql
    ALTER TABLE docs ALTER COLUMN embedding TYPE vector(<new_dim>);
    ```

---

## Reset after embedding update

If you change `AGENT_EMBED_MODEL` or `AGENT_EMBED_DIM`, you must ensure the database vector dimension matches the embedding dimension. Easiest path is to recreate the DB volume (it is auto-initialized from `db/schema.sql`).

1. Edit `.env` to the new model & dim (defaults use `all-minilm`):

   * `AGENT_EMBED_MODEL=all-minilm`
   * `AGENT_EMBED_DIM=384`
2. Edit `db/schema.sql` to match:

   ```sql
   -- docs table
   embedding VECTOR(384)
   ```
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

> Prefer a migration instead of dropping the volume? Run:
>
> ```sql
> ALTER TABLE docs ALTER COLUMN embedding TYPE vector(384);
> ```

## How it works (core concepts)

### 1) The Agent loop (`agent/core.py`)

* Builds a message list: a **system prompt** + **user task** + any **observations** from tools.

* Calls the LLM with the messages.

* The LLM responds with a **JSON tool call** or a **final answer**. Example:

  ```json
  {"tool":"search","input":{"query":"NVIDIA founders"}}
  ```

* The agent executes the tool, captures an observation (and often a short LLM summary), appends that back to the conversation, and repeats until a final answer or the step limit is reached.

### 2) The LLM client (`agent/llm.py`)

* Thin async wrapper around **Ollama**:

  * `/api/chat` for conversation
  * `/api/embeddings` for vectorization
* Normalizes messages and enforces length limits.
* Includes a **system prompt** that teaches the LLM to return JSON tool calls or a final JSON with a readable summary.

### 3) Tools (`agent/tools.py`)

* **Search**: Serper (or your stub). Returns top results.
* **Fetch**: Gets a URL, returns `{title, url, text}`.
* **ETL**: `load_csv/json` → `transform` (select/rename/limit) → `save`.
* **Memory**: router that calls the configured memory implementation.

Each tool is **pure async** and returns structured data.

### 4) Memory (`agent/memory/`)

Two implementations (same interface):

* `SimpleMemory` (in-process, substring search). Great for unit tests.
* `PgVectorMemory` (Postgres + pgvector, cosine similarity):

  * `aupsert(docs)` store content + embeddings
  * `aquery(query, k)` vector search
  * `aadd(text, source, uri, meta)` quick note
  * `adump(n)` latest notes dump (for system prompt context)

**Embeddings** come from Ollama (`AGENT_EMBED_MODEL`), and the vector size must match DB schema.

### 5) REPL (`agent/repl.py`)

* Rich header + tool hints + keybindings
* History, completion for commands and file paths
* Commands:

  * `/research <question>`
  * `/etl -p <path> -t "<transform>" [-l <out>]`
  * `/etl_from_source -p <url> -t "<transform>" [-l <out>]`
  * `/where <path>`
  * `/mcp ...` (see below)
  * `/help`, `exit()`

---

## Using the REPL

Start it:

```bash
docker compose run --rm app
```

Try research:

```
/research Compare NVIDIA vs AMD GPU market share over the last 2 years.
```

Try ETL:

```
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3"
```

Check a path:

```
/where ./data/sales_orders.csv
```

### Examples (commands & expected output)

> These are abridged. Exact wording may vary, but the flow (MODEL → TOOL CALL/RESULT → final answer) should look similar.

**Research**

```
/research Who founded NVIDIA and when?
```

*Expected excerpt:*

```
Step 1/8
MODEL: {"tool":"search","input":{"query":"NVIDIA founders"}}
TOOL CALL: search {"query": "NVIDIA founders"}
TOOL RESULT: search → (top links...)
...
Answer
NVIDIA was founded in 1993 by Jensen Huang, Chris Malachowsky, and Curtis Priem.

Sources:
- Wikipedia — https://en.wikipedia.org/wiki/Nvidia
- NVIDIA Corporate Timeline — https://www.nvidia.com/en-us/about-nvidia/corporate-timeline/
```

**ETL (local file)**

```
/etl -p ./data/sales_orders.csv -t "reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3" -l ./data/sales_orders.sample.parquet
```

*Expected excerpt:*

```
TOOL RESULT: etl → ETL DONE: saved ./data/sales_orders.sample.parquet (rows: 3)
```

**ETL from a URL**

> Tip: Browse free CSV/JSON datasets at Data.gov’s catalog: [https://catalog.data.gov/dataset/](https://catalog.data.gov/dataset/) . Use a **direct** CSV/JSON file URL.

```
/etl_from_source -p https://people.sc.fsu.edu/~burkardt/data/csv/airtravel.csv \
  -t "reorder:Month,1958,1959; rename:1958->y1958; limit:5" \
  -l ./data/airtravel_top5.csv
```

*Expected excerpt:*

```
TOOL RESULT: etl → ETL DONE: saved ./data/airtravel_top5.csv (rows: 5)
```

**MCP file listing**

```
/mcp add-http -n fs -u http://host.docker.internal:8765
/mcp call fs list_files '{"path":"./data"}'
```

*Expected excerpt:*

```
TOOL RESULT: mcp:fs:list_files → ["customers.json","sales_orders.csv", ...]
```

**Path helper**

```
/where ./data/customers.json
```

*Expected excerpt:*

```
Resolved: /app/data/customers.json (exists, ~2.4 KB)
```

---

## ETL mini-DSL

> Tip: Need a URL for `/etl_from_source`? Browse open datasets at **Data.gov**: [https://catalog.data.gov/dataset/](https://catalog.data.gov/dataset/) — copy a **direct** CSV/JSON link.

Transform spec (semicolon-chained):

* `reorder: A,B,C` — selects & orders; missing columns are appended in original order
* `rename: old->new, 'unit price'->price` — rename columns/keys
* `limit: 100` — cap output rows (or JSON item count)

Examples:

```
reorder:date,region,product,units,unit_price; rename:unit_price->price; limit:3
rename:'1958'->y1958,'unit price'->price
```

Output format is inferred from `-l` extension, or mirrors input if omitted.

---

## MCP (Model Context Protocol) tools

An **HTTP façade** service exposes simple FS tools (list/read/write/stat). We include `mcpfs` in Compose.

In the REPL:

```
/mcp add-http -n fs -u http://host.docker.internal:8765
/mcp tools
/mcp call fs list_files '{"path":"./data"}'
/mcp call fs read_text '{"path":"./data/customers.json"}'
```

* `mcp list` — show connected servers (marks the default)
* `mcp default <name>` — set default server
* `mcp ping [<name>]` — connectivity check (counts usable tool endpoints)
* `mcp remove <name>` — disconnect

---

## Troubleshooting

* **Ollama timeouts** (ReadTimeout):

  * Make sure Ollama is running on your host and the model is pulled.
  * Try `curl http://host.docker.internal:11434/api/tags` from **inside** a container:

    ```bash
    docker compose run --rm --entrypoint curl app -sS http://host.docker.internal:11434/api/tags
    ```
  * If you use a different host/port, set `OLLAMA_HOST` in `.env`.

* **Embedding dimension mismatch**:

  * `AGENT_EMBED_DIM` must equal the `vector(<dim>)` in `schema.sql` (`docs.embedding`).
  * Fix by changing both and recreating the DB volume (or run a migration).

* **MCP 400 errors**:

  * Make sure you pass JSON payloads as single-quoted strings:

    ```
    /mcp call fs list_files '{"path":"./data"}'
    ```

* **Windows quoting**:

  * Use double quotes for outer strings and escape inner quotes, or run the REPL queries without shell involvement.

---

## Project layout

```
.
├─ app/
│  ├─ agent/
│  │  ├─ __init__.py
│  │  ├─ __main__.py          # Typer entrypoint → REPL
│  │  ├─ core.py              # Agent loop (LLM ↔ tools ↔ memory)
│  │  ├─ llm.py               # Ollama chat + embeddings
│  │  ├─ tools.py             # search, fetch, etl, memory router
│  │  ├─ repl.py              # interactive CLI (Rich + prompt_toolkit)
│  │  ├─ schemas.py           # Message, ToolCall, StepResult models
│  │  ├─ config.py            # settings (.env)
│  │  └─ memory/
│  │     ├─ __init__.py       # get_memory() selector
│  │     ├─ simple_memory.py  # SimpleMemory (substring search)
│  │     ├─ pg_store.py       # PgVectorMemory (cosine similarity)
│  │     └─ types.py          # Protocol / shared types
│  ├─ data/                   # sample datasets for ETL (container mount)
│  ├─ Dockerfile              # app image
│  └─ requirements.txt
├─ data/                      # shared sample datasets (bind-mounted)
├─ db/
│  └─ schema.sql              # pgvector bootstrap (mounted by pgvector)
├─ servers/
│  └─ fs-mcp-server/          # MCP HTTP FS tool (Node)
│     ├─ index.js
│     ├─ package.json
│     └─ README.md
├─ compose.yml                # Docker Compose for app + deps
├─ .env.example               # example env; copy to .env
└─ README.md
```

**Notes**

* Compose mounts **`db/schema.sql`** into Postgres to enable pgvector and create the `docs` table.
* The REPL and agent live under `app/agent`. Root-level `data/` is bind-mounted into the container so you can point ETL commands at local files.
* The optional MCP FS server (`servers/fs-mcp-server`) exposes `list_files`, `read_text`, `write_text`, and `stat` over HTTP; connect from the REPL with `/mcp add-http ...`.

## What to read next

* `agent/core.py` — see how the loop parses JSON tool calls and stitches observations back into the conversation.
* `agent/llm.py` — how we shape prompts and talk to Ollama (chat + embeddings).
* `agent/tools.py` — add your own tool; keep it async and return structured data.
* `agent/memory/pg_store.py` — vector upsert/query with Postgres.

Have fun hacking!
