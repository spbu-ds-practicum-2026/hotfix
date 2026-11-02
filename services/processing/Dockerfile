FROM python:3.12-slim

WORKDIR /app

COPY services/processing/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/processing/src /app/services/processing/src
COPY services/storage/src /app/services/storage/src

ENV PYTHONPATH=/app/services/processing/src:/app/services/storage/src

CMD ["python", "/app/services/processing/src/main.py"]