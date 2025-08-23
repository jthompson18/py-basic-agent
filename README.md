# py-basic-agent
## Run with Dockerized Ollama (recommended on non-Mac devices)
1. `cp .env.example .env` (keep `OLLAMA_BASE_URL=http://ollama:11434`)
2. `make run-docker`

## Run with Host Ollama (macOS)
1. Install and run Ollama natively:
   - `brew install ollama` (or download app), then:
   - `ollama serve` (in a separate terminal)
   - `ollama pull llama3.1:8b && ollama pull nomic-embed-text`
2. `cp .env.example .env` and set `OLLAMA_BASE_URL=http://host.docker.internal:11434`
3. `make run-host`

## (Optional) Enable pgvector
- Add `--profile pgvector` or use the `*-pg` Make targets.
