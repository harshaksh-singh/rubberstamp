# syntax=docker/dockerfile:1
# The RubberStamp helpdesk OpenEnv server. Build context: this package (integrations/openenv/rubberstamp_env), after
# `sh scripts/vendor_wheels.sh`, which writes vendor/: the rs-* wheels (the context cannot reach ../../../packages)
# and requirements.txt, the third-party pins with hashes from `uv export --frozen` of this project's uv.lock.
#
#   sh scripts/vendor_wheels.sh
#   docker build -f server/Dockerfile -t rubberstamp-env:local .
#   docker run --rm -p 127.0.0.1:18500:8000 rubberstamp-env:local
#
# Not ghcr.io/huggingface/openenv-base: its `latest` (the only named tag; the others are sha-<commit>) runs Python
# 3.11.17, and the rs-* packages need Python 3.12 or later. Both base images are pinned by digest.
ARG PYTHON_IMAGE=python:3.12.16-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE}
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH

# Third-party dependencies by exact version and hash; then the rs-* wheels from the context only (no index).
COPY vendor/ /tmp/vendor/
RUN uv venv /app/.venv --python /usr/local/bin/python3 \
 && uv pip install --require-hashes --no-deps -r /tmp/vendor/requirements.txt \
 && uv pip install --no-index --no-deps /tmp/vendor/*.whl \
 && rm -rf /tmp/vendor

WORKDIR /app/env
COPY __init__.py models.py client.py openenv.yaml ./
COPY server/ server/

# Build every seeded world once at build time and print the digests, to compare with results/certify/world_digests.json.
RUN python -c "from rs_suite.conformance import world_digests; import json; print(json.dumps(world_digests()))"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin rubberstamp
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
