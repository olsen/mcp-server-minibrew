#!/bin/sh
# Starts the MCP server. The token is read from .env on every call (not exported here),
# so pasting a fresh one into .env works without restarting.
cd "$(dirname "$0")"
export MINIBREW_ENV_FILE="$PWD/.env"
exec ./.venv/bin/mcp-server-minibrew
