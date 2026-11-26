FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

RUN mkdir -p /app/storage/uploads /app/storage/temp /app/storage/outputs

EXPOSE 8080

CMD exec uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port ${PORT:-8080} --forwarded-allow-ips='*'
