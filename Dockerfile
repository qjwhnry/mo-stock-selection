FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml ./
COPY config/ config/
COPY src/ src/
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "mo_stock.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
