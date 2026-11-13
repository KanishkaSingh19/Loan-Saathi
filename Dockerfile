# API container for a free Hugging Face Docker Space (or any container host).
# Serves the webhook, the speech proxy, /health and the dashboard (built in the first stage).
# Stage 1: build the dashboard, so one container serves the API, the dashboard and live voice.
FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
# Same origin as the API: VITE_API_BASE stays empty.
RUN npm run build

FROM python:3.12-slim

# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face runs Spaces as user 1000.
RUN useradd -m -u 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app src/ src/
COPY --chown=app config/ config/
COPY --chown=app models/voice_model.txt models/voice_model_meta.json models/
# Evidence for the Evaluation Lab: metrics, validation and benchmark reports, the extraction cases.
COPY --chown=app models/metrics.json models/validation_report.json models/comparison.json models/experiment_features.json models/
COPY --chown=app reports/ reports/
COPY --chown=app tests/extraction_cases.json tests/
COPY --from=dashboard --chown=app /dashboard/dist dashboard/dist
USER app

# Secrets (WEBHOOK_KEY, DATABASE_URL, Gnani keys) come from the host's settings, never the image.
ENV HOST=0.0.0.0 PORT=7860 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python", "-X", "utf8", "-W", "ignore", "src/server.py"]
