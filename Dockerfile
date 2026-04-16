FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG APP_UID=1000
ARG APP_GID=1000

RUN set -eu; \
    if [ "${APP_UID}" = "0" ]; then APP_UID=1000; fi; \
    if [ "${APP_GID}" = "0" ]; then APP_GID=1000; fi; \
    groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app

COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

RUN mkdir -p /app/runtime /app/data \
    && chown -R app:app /app/runtime /app/data

USER app

EXPOSE 8501

CMD ["python", "main.py"]
