FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencias de Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libcups2 \
    libasound2 libx11-xcb1 libxcursor1 libxss1 libxext6 fonts-liberation \
    ca-certificates wget gnupg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN python -m playwright install chromium

COPY . .

# Ajusta el comando si quieres otros flags (--retries, --failed-csv, etc.).
CMD ["python", "main.py", "--csv", "data.csv", "--failed-csv", "failed_cmp.csv"]
