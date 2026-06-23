FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_ENGINE=sqlite \
    SQLITE_PATH=/data/db.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py ensure_admin_user --only-if-no-admin && daphne -b 0.0.0.0 -p 8000 config.asgi:application"]
