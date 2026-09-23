FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest_worker.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "ingest_worker.py"]
