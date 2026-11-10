FROM python:3.12-slim

WORKDIR /app

COPY services/api/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    for i in 1 2 3 4 5 6 7 8 9 10; do \
        pip install --default-timeout=300 --retries=10 -r /app/requirements.txt && exit 0; \
        echo ">>> pip failed, retry $i"; sleep 3; \
    done; exit 1

COPY services/api/src /app/services/api/src
COPY services/storage /app/services/storage
COPY services/domain/src /app/services/domain/src
COPY services/processing/src/contracts /app/services/processing/src/contracts

ENV PYTHONPATH=/app/services/api/src:/app/services/storage/src:/app/services/domain/src:/app/services/processing/src

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
