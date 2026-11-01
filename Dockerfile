# One image for the api (and later the migrate and agent services).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api ./api

RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
# Exec form so uvicorn is PID 1 and receives SIGTERM for graceful shutdown.
# One worker: the alert engine runs inside the api process (see SPEC.md).
CMD ["uvicorn", "--factory", "api.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
