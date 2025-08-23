# Default: host Ollama + pgvector (both enabled by default in compose)
run:
	# REPL by default (thanks to CMD ["repl"])
	docker compose run --rm app

run-quiet:
	docker compose run --rm app --no-verbose

task:
	# one-shot task still works
	docker compose run --rm app -t "Who founded NVIDIA and when?"

# Use dockerized Ollama instead of host
run-docker-ollama:
\t# set OLLAMA_BASE_URL=http://ollama:11434 in .env first
\tdocker compose --profile ollama-docker up --build

sh:
\tdocker compose exec app bash