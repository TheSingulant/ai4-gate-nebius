FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 gate \
    && mkdir -p /app/data /app/evidence \
    && chown -R gate:gate /app

USER gate

ENV PYTHONDONTWRITEBYTECODE=1 \
    PLANNER=auto \
    AUTHORIZER=reference \
    HOST=0.0.0.0 \
    PORT=8000 \
    GATE_BUDGET_DB=/app/data/budget.sqlite3 \
    GATE_SESSION_ROOT=/app/data/sessions \
    GATE_DAILY_REQUEST_BUDGET=40 \
    GATE_DAILY_TOKEN_BUDGET=200000 \
    GATE_DAILY_AGENT_RUNS=100 \
    GATE_MODE=guided \
    GATE_LIVE_INTERVAL_SECONDS=600

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "gate_demo.server", "--host", "0.0.0.0", "--port", "8000"]
