FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic needs a key to import settings, but the value is never used at runtime.
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home appuser && mkdir -p /app/media && chown -R appuser /app/media
USER appuser

EXPOSE 8000

# Apply migrations, then serve. (For several instances, run migrate as a separate release step.)
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn shipit.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 3"]