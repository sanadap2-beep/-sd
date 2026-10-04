FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# pg_dump للنسخ الاحتياطي على PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists

COPY . .

# تشغيل غير-root + فحص صحة
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8080), timeout=5)" || exit 1

CMD ["python", "bot.py"]
