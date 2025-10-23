FROM python:3.11-slim

# ─── system packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

# ─── uv ────────────────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir uv

COPY . /app
WORKDIR /app

# ─── Install everything ────────────────────────────────────────────────────────
RUN uv sync --frozen --no-dev

# ─── runtime config ────────────────────────────────────────────────────────────
EXPOSE 8081
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8081"]
