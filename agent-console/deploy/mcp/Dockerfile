# MCP tool server — Cloud Run sibling.
#
# Build:  gcloud builds submit --tag europe-west1-docker.pkg.dev/$PROJECT/agents/mcp-45black-tools
# Run:    gcloud run deploy mcp-45black-tools --image ... --region europe-west1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN pip install --no-cache-dir fastmcp httpx

COPY server.py .

CMD exec python server.py
