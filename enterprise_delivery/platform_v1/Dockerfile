FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY enterprise_data_platform ./enterprise_data_platform
RUN pip install --upgrade pip && pip install . uvicorn

EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=3)"

CMD ["uvicorn", "enterprise_data_platform.reference_app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
