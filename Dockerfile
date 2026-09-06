# OpenManus — Render-ready image
#
# Two-stage would be nicer but Render free tier benefits from a single
# slim layer.  We swap the upstream `CMD ["bash"]` for `render_main.py`
# so Render's Web Service has a long-running HTTP process to health-check.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /app/OpenManus

# System deps — curl for healthcheck, git in case requirements pull from VCS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast installer) if missing.
RUN command -v uv >/dev/null 2>&1 || pip install --no-cache-dir uv

COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

# Generate a stub config.toml from the example if the real one is missing.
# render_main.py / app code will read config/config.toml at startup.
COPY config /app/OpenManus/config
# If config.toml is absent at runtime, render_main should still boot —
# the app's Config loader falls back to config.example.toml automatically.
# We copy the example as a safety net so Render doesn't crash on missing file.
RUN if [ ! -f config/config.toml ] && [ -f config/config.example.toml ]; then \
        cp config/config.example.toml config/config.toml; \
    fi

COPY . .

EXPOSE 10000

# Healthcheck uses the FastAPI /healthz endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:10000/healthz || exit 1

# Materialise runtime env into config/config.toml, then boot uvicorn.
CMD ["sh", "-c", "python render_envsubst.py && exec python -m uvicorn render_main:app --host 0.0.0.0 --port 10000"]
