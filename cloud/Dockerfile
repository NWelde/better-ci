FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml

RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir .

COPY app /app/app
COPY schema.sql /app/schema.sql

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
