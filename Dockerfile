# syntax=docker/dockerfile:1
FROM node:22.11-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TENSOR_STATIC_DIR=/app/static
RUN groupadd -r tensor && useradd -r -g tensor -d /app tensor
WORKDIR /app
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ ./
COPY --from=web /web/dist ./static
RUN mkdir -p /app/var/objects && chown -R tensor:tensor /app/var
USER tensor
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
# Default: API. Override the command to run a worker: python -m tensor_ops.jobs.worker --kinds ingest_evidence
CMD ["uvicorn", "tensor_ops.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
