# Recipe used to package the collector, Telegram bot and read-only web feed.
# The block is selected by the BLOCK variable (bot, collector or web).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
# Without BLOCK, the entry point reports an unknown block.
CMD ["sh", "-c", "exec python main.py $BLOCK"]
