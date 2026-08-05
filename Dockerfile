# ==============================================================================
# Stage 1: Builder — install dependencies into a virtual environment
# ==============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# Stage 2: Production — minimal runtime image
# ==============================================================================
FROM python:3.12-slim AS production

LABEL maintainer="BoardMatch Team"
LABEL org.opencontainers.image.source="https://github.com/codess-aus/BoardMatch"

# Install curl for health checks
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application code and migrations
COPY boardmatch/ ./boardmatch/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# Ensure migration script is executable
RUN chmod +x ./scripts/migrate.sh

# Switch to non-root user
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "boardmatch.api:app", "--host", "0.0.0.0", "--port", "8000"]
