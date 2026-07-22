FROM python:3.12-bookworm

LABEL org.opencontainers.image.source="https://github.com/KZI-22/xhs_mcp"
LABEL org.opencontainers.image.description="Local, single-user, read-only Xiaohongshu MCP server"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    XHS_BROWSER_CHANNEL=chromium

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8765), 3).close()"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["xhs-read-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8765", "--auth-state-path", "/data/chrome-storage_state.json"]
