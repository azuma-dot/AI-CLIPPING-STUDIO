FROM python:3.12-slim
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

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

RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:/opt/render/.deno/bin:${PATH}"


# --- Auto-patched bgutil PO Token provider (v2.0.2) ---
RUN apt-get update && apt-get install -y --no-install-recommends curl git ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --single-branch --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server && npm ci && npx tsc \
    && chmod -R a+rX /opt/bgutil
RUN pip install --no-cache-dir -U "yt-dlp[default]" bgutil-ytdlp-pot-provider==2.0.2 \
    || pip install --no-cache-dir --break-system-packages -U "yt-dlp[default]" bgutil-ytdlp-pot-provider==2.0.2
CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & sleep 2 && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
