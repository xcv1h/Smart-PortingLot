# Pinned minor version: a new Python release can't silently change the build.
FROM python:3.13-alpine

# PYTHONUNBUFFERED: write logs straight to stdout instead of buffering them, so
#   `docker compose logs` shows events as they happen.
# PYTHONDONTWRITEBYTECODE: don't create __pycache__ (the non-root user below
#   couldn't write it anyway, and it is not needed).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies in their own layer, before copying the code, so Docker
# only re-runs pip when requirements.txt changes. --no-cache-dir keeps pip's
# download cache out of the image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the application code goes in: no tests, no dev dependencies.
COPY app ./app

# Don't run as root inside the container. Alpine's adduser:
#   -D  no password, -H  no home directory (nothing is written at runtime),
#   -u  fixed uid, so USER can be numeric (hadolint DL3066) and still be
#       `parking` in `docker exec ... whoami`.
RUN adduser -D -H -u 1000 parking
USER 1000

EXPOSE 8000
# --root-path /api: nginx strips the /api prefix before proxying to us. Telling
# uvicorn about it lets the interactive docs, opened at /api/docs through the
# proxy, build the right link to /api/openapi.json.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api"]
