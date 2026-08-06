# ==============================================================================
# Base image pinning strategy
# ------------------------------------------------------------------------------
# Pinned to a specific linux/amd64 python:3.12-slim digest (not just the
# mutable "3.12-slim" tag) so builds are reproducible and immune to upstream
# tag repointing/supply-chain surprises. Re-pin periodically by resolving the
# current digest, e.g.:
#   docker buildx imagetools inspect python:3.12-slim --format '{{json .Manifest}}'
# A Dependabot "docker" ecosystem entry (see .github/dependabot.yml) is
# configured to open PRs automatically when a newer patch-level image is
# published upstream — review and re-pin the digest via that PR rather than
# editing it by hand.
# ==============================================================================
ARG BASE_IMAGE=python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64

# ==============================================================================
# Stage 1: Builder — install dependencies into a virtual environment
# ==============================================================================
FROM ${BASE_IMAGE} AS builder

WORKDIR /build

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ==============================================================================
# Stage 2: Production — minimal runtime image
# ==============================================================================
FROM ${BASE_IMAGE} AS production

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

# ------------------------------------------------------------------------------
# Read-only root filesystem compatibility
# ------------------------------------------------------------------------------
# This image writes nothing to /app at runtime, so it is safe to run the
# container with `--read-only` (or `security_opt`/`read_only: true` in
# Compose/Kubernetes). The only writable paths the process may need are:
#   * /tmp                — gunicorn's default worker heartbeat temp files,
#                            and any stdlib tempfile usage. Mount a tmpfs here
#                            when running read-only, e.g. `--tmpfs /tmp`.
#   * /home/appuser        — only needed if a library insists on a writable
#                            HOME (e.g. for caches); not required in normal
#                            operation. Mount a tmpfs here too if it errors.
# No application data is persisted inside the container; all state lives in
# the database, so a read-only root filesystem is the recommended runtime
# posture in production.
# ------------------------------------------------------------------------------
# Least privilege: drop all Linux capabilities the app doesn't need when
# running this container, e.g. `--cap-drop=ALL` and `--security-opt=no-new-privileges`.
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# ------------------------------------------------------------------------------
# Process manager: gunicorn + uvicorn.workers.UvicornWorker
# ------------------------------------------------------------------------------
# Plain `uvicorn` (single process) has no built-in worker supervision — if a
# worker hangs or crashes there's nothing to restart it, and it only uses one
# CPU core. Gunicorn is used here as the production process manager because it
# supervises multiple Uvicorn-worker subprocesses, restarts workers that die,
# and gives us `--graceful-timeout`/`--timeout` controls for clean rolling
# deploys. (Uvicorn's own `--workers` flag is a lighter-weight alternative if
# you want to avoid the extra dependency, but it lacks gunicorn's worker
# supervision/auto-restart and arbiter model, which matters more here than the
# small footprint savings.)
#
# WEB_CONCURRENCY controls the worker count and should be set by the operator
# based on available CPU (a common formula is `2 * num_cores + 1`); it
# defaults to 4 if unset, a reasonable starting point for small/medium
# instances. GUNICORN_TIMEOUT/GUNICORN_GRACEFUL_TIMEOUT bound how long a
# worker may run a request before being killed, and how long gunicorn waits
# for in-flight requests to finish after a TERM signal — tune both alongside
# your deploy/orchestrator's shutdown grace period so rolling deploys don't
# abruptly kill active requests.
ENV WEB_CONCURRENCY=4 \
    GUNICORN_TIMEOUT=30 \
    GUNICORN_GRACEFUL_TIMEOUT=30 \
    GUNICORN_KEEPALIVE=5

CMD ["sh", "-c", "exec gunicorn boardmatch.api:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-4} --timeout ${GUNICORN_TIMEOUT:-30} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --keep-alive ${GUNICORN_KEEPALIVE:-5} --access-logfile - --error-logfile -"]
