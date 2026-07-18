#!/bin/sh
set -eu

data_dir="${XHS_DATA_DIR:-/data}"
token_file="${XHS_MCP_TOKEN_FILE:-${data_dir}/mcp_token}"

mkdir -p "$data_dir"

if [ -z "${XHS_MCP_AUTH_TOKEN:-}" ]; then
    if [ ! -s "$token_file" ]; then
        umask 077
        python -c "import secrets; print(secrets.token_urlsafe(32))" > "$token_file"
    fi
    XHS_MCP_AUTH_TOKEN="$(tr -d '\r\n' < "$token_file")"
    export XHS_MCP_AUTH_TOKEN
fi

printf '%s\n' "xhs-read-mcp is starting"
printf '%s\n' "MCP URL: http://127.0.0.1:8765/mcp"
printf '%s\n' "MCP Bearer Token: ${XHS_MCP_AUTH_TOKEN}"

exec "$@"
