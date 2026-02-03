FROM python:3.11-slim AS base

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]"

# Copy source
COPY src/ src/
COPY tests/ tests/
COPY docs/ docs/

# Create data directory
RUN mkdir -p data

# Run tests as build validation
RUN pytest -v --tb=short

# Production stage
FROM python:3.11-slim AS production

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
RUN mkdir -p data

# Non-root user
RUN useradd --create-home appuser
RUN chown appuser:appuser data
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--mode=paper"]
