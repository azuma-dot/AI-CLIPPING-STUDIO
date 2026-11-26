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
# === CLOUDFLARE WARP + DENO FOR YT-DLP ===
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /usr/local/bin/wgcf "https://github.com/ViRb3/wgcf/releases/download/v2.3.1/wgcf_2.3.1_linux_amd64" \
    && chmod +x /usr/local/bin/wgcf \
    && mkdir -p /tmp/wp \
    && curl -fsSL "https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_amd64.tar.gz" | tar -xz -C /tmp/wp \
    && find /tmp/wp -name wireproxy -type f -exec mv {} /usr/local/bin/wireproxy \; \
    && chmod +x /usr/local/bin/wireproxy \
    && rm -rf /tmp/wp \
    && (pip install --no-cache-dir -U "yt-dlp[default]" pysocks || pip install --no-cache-dir --break-system-packages -U "yt-dlp[default]" pysocks)
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh
CMD ["sh", "-c", "/usr/local/bin/start.sh uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
