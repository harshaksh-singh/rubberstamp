# The helpdesk sidecar image (rs-harbor-helpdesk:0.1.0): one instance's world behind rs-world's 16 MCP tools.
# It has rs-core and rs-world with its MCP server, and no rs-verify, no rs-suite, no specs and no graders.
# Build from the repository root (local tag only, never pushed):
#   docker build -f integrations/harbor/images/helpdesk.Dockerfile -t rs-harbor-helpdesk:0.1.0 .
# Dependencies are installed from integrations/harbor/uv.lock (`uv sync --frozen`: pinned versions, hashes checked).
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/rs UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
WORKDIR /src
COPY packages/ packages/
COPY integrations/harbor/ integrations/harbor/
RUN cd integrations/harbor && uv sync --frozen --no-dev --no-editable --extra sidecar --python /usr/local/bin/python3.12

FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
RUN useradd --system --uid 10001 --create-home rs && mkdir -p /var/rs && chown rs:rs /var/rs
COPY --from=build /opt/rs /opt/rs
ENV PATH=/opt/rs/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY results/certify/world_digests.json /opt/rs/world_digests.json
# Every instance seed's world and the tool manifest must hash to the committed digests, or the build fails; and the
# graders must not be in this image.
RUN python -m rs_harbor.worlds /opt/rs/world_digests.json \
 && if python -c "import rs_suite" 2>/dev/null; then echo "rs-suite must not be in the helpdesk image" >&2; exit 1; fi
USER rs
WORKDIR /home/rs
EXPOSE 8000
CMD ["rs-harbor-serve", "--host", "0.0.0.0", "--port", "8000"]
