# Multi-stage: build the SPA with node, then serve it and the API from one
# Python image. The worker runs from this same image with a different command.

# --- stage 1: build the frontend -----------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- stage 2: python runtime ---------------------------------------------
FROM python:3.13-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVE_STATIC=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY knowledge_desk ./knowledge_desk
COPY migrations ./migrations
COPY check_setup.py entrypoint.sh ./
COPY --from=web /web/dist ./frontend/dist
RUN chmod +x entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "knowledge_desk.main:app", "--host", "0.0.0.0", "--port", "8000"]
