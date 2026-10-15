FROM python:3.12-slim
WORKDIR /app
# Fonts for boss images
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
RUN mkdir -p /data
ENV DATABASE_PATH=/data/bot.db
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s CMD ["python", "-m", "bot.health"]
CMD ["python", "-m", "bot.main"]
