FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.production.lock ./
RUN python -m pip install --require-hashes -r requirements.production.lock
COPY enterprise_data_platform ./enterprise_data_platform
USER 10001:10001

EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez', timeout=3)"

CMD ["uvicorn", "enterprise_data_platform.production_app:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--limit-concurrency", "64", "--timeout-graceful-shutdown", "35", "--no-access-log"]
