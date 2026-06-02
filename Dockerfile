# ── Base image: Python 3.11 slim (Debian Bookworm) ────────────────────────────
FROM python:3.11-slim-bookworm

# ── System deps untuk Playwright Chromium ────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime deps
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libx11-xcb1 \
    libxcb1 libxext6 libxss1 libxtst6 fonts-liberation \
    # curl_cffi / general deps
    libssl-dev curl wget ca-certificates \
    # pillow-heif
    libheif-dev libde265-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Install Playwright + Chromium browser ─────────────────────────────────────
RUN playwright install chromium --with-deps

# ── Copy seluruh project ──────────────────────────────────────────────────────
COPY . .

# ── Port yang di-expose ───────────────────────────────────────────────────────
EXPOSE 8080

# ── Start command (sama seperti Procfile Railway) ─────────────────────────────
CMD ["gunicorn", "server:app", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "120"]
