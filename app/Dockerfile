FROM python:3.11-slim

# System deps for lxml/readability
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
    libxml2-dev libxslt1-dev \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/app
# ensure you also have:
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install -r /tmp/requirements-dev.txt

COPY agent ./agent
ENV PYTHONUNBUFFERED=1

# Always start the agent; default subcommand = REPL
ENTRYPOINT ["python","-m","agent"]
CMD ["repl"]
