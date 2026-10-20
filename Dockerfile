FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cloud platforms inject $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Single process so the in-process background worker has exactly one owner.
# (See README for how to scale the worker out separately if ever needed.)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
