FROM python:3.13-slim

WORKDIR /app

# Tesseract OCR (para leer fotos de tickets/facturas)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/

WORKDIR /app/backend

ENV TMS_HOST=0.0.0.0 \
    TMS_PORT=8750

EXPOSE 8750

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8750"]
