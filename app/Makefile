# Default: host Ollama + pgvector (both enabled by default in compose)
run:
\tdocker compose up --build

# Use dockerized Ollama instead of host
run-docker-ollama:
\t# set OLLAMA_BASE_URL=http://ollama:11434 in .env first
\tdocker compose --profile ollama-docker up --build

sh:
\tdocker compose exec app bash

test:
\tdocker compose run --rm app pytest -q
