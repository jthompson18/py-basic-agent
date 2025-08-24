from pydantic import BaseModel
import os


class Settings(BaseModel):
    serper_api_key: str | None = os.getenv("SERPER_API_KEY")
    ollama_base_url: str = os.getenv(
        "OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model: str = os.getenv("MODEL", "llama3.1:8b")
    embed_model: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
    max_steps: int = int(os.getenv("MAX_STEPS", "8"))
    temperature: float = float(os.getenv("TEMPERATURE", "0.2"))
    memory_backend: str = os.getenv("MEMORY_BACKEND", "pgvector")
    # pg
    pghost: str = os.getenv("PGHOST", "pgvector")
    pgport: int = int(os.getenv("PGPORT", "5432"))
    pguser: str = os.getenv("PGUSER", "agent")
    pgpassword: str = os.getenv("PGPASSWORD", "agent")
    pgdatabase: str = os.getenv("PGDATABASE", "agentdb")


settings = Settings()
