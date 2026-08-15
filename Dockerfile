FROM python:3.12-slim AS base

# Do not run as root. The pod security context sets this too, but an image
# that only works as root is a problem waiting to happen.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

# Dependencies first so a code change does not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
