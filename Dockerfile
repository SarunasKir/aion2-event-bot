FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
RUN mkdir -p /data
ENV DATABASE_PATH=/data/bot.db
CMD ["python", "-m", "bot.main"]
