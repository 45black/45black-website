# 45black Agent Console — Cloud Run image.
#
# Build:  gcloud builds submit --tag europe-west1-docker.pkg.dev/$PROJECT/agents/console
# Run:    gcloud run deploy console --image ... --region europe-west1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# System deps: minimal — we're FastAPI + google-genai + google-cloud-firestore.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY agents ./agents
COPY mcp ./mcp

# Cloud Run defaults the listen port to 8080 via $PORT. uvicorn honours it.
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
