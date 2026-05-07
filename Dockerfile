FROM python:3.12-slim

WORKDIR /app

# Use Aliyun mirror for apt (much faster in China)
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

# Install system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# --- Layer 1: install Python dependencies (cached unless pyproject.toml changes) ---
COPY pyproject.toml README.md ./
# Create a dummy package so pip can resolve deps without real source code
RUN mkdir -p src/mo_stock && touch src/mo_stock/__init__.py \
    && pip install --no-cache-dir -e . \
    && rm -rf src/

# --- Layer 2: copy real source code (changes often, but pip install is already cached) ---
COPY config/ config/
COPY src/ src/
COPY alembic.ini ./
COPY alembic/ alembic/

EXPOSE 8000
CMD ["uvicorn", "mo_stock.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
