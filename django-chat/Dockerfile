FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000
# Daphne speaks HTTP and WebSockets. Scale by running more of these containers.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
