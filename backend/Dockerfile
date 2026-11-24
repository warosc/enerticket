# Imagen única del backend: API (por defecto), worker y beat de Celery.
#   api    -> alembic upgrade head + seeds + uvicorn (CMD por defecto)
#   worker -> command: celery -A app.tasks.celery_app worker --loglevel=info
#   beat   -> command: celery -A app.tasks.celery_app beat --loglevel=info
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /code

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /code --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["api"]
