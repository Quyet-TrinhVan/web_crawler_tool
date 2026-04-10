FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    NOVNC_PORT=6080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fluxbox \
    novnc \
    supervisor \
    websockify \
    x11vnc \
    xvfb \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt fastapi pydantic "uvicorn[standard]"

COPY . .
COPY docker/supervisord.conf /etc/supervisor/conf.d/web-crawler.conf

RUN mkdir -p /app/browser_state /var/log/supervisor

EXPOSE 8000 6080

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/web-crawler.conf"]
