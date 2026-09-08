FROM python:3.11-slim

WORKDIR /opt/render/project/src

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*

COPY trading-monitors/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY trading-monitors/ /opt/render/project/src/trading-monitors/
WORKDIR /opt/render/project/src/trading-monitors

ENV PORT=10001
EXPOSE 10001

HEALTHCHECK --interval=5m --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

CMD ["python3", "-u", "render_monitors.py"]
