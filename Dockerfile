FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY paperark ./paperark
COPY LICENSE README.md DESIGN.md BENCH.md ./
# PAPERARK_BASE_URL: URL pública que se imprime en las portadas y en los QR
ENV PAPERARK_BASE_URL=""
EXPOSE 8000
CMD ["uvicorn", "paperark.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
