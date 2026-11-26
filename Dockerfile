FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend
RUN mkdir -p /app/storage

WORKDIR /app/backend
ENV PORT=8080
EXPOSE 8080

RUN pip install --no-cache-dir --upgrade pip "yt-dlp[default]"
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:/opt/render/.deno/bin:${PATH}"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]