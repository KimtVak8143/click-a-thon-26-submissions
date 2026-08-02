# Build stage with uv
FROM python:3.12-slim AS builder

# Install uv
RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Runtime stage
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY app ./app
COPY migrations ./migrations
COPY docs ./docs
COPY scripts ./scripts
COPY pyproject.toml ./

# Make scripts executable
RUN chmod +x scripts/*.sh

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose ports (FastAPI + MCP)
EXPOSE 8080 8002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:${PORT:-8080}/health', timeout=5.0)" || exit 1

# Start command (using railway-start.sh for multi-process management)
CMD ["bash", "scripts/railway-start.sh"]
