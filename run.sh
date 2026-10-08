#!/bin/sh
# Starts the MCP server. The token is read from .env on every call (not exported here),
# so pasting a fresh one into .env works without restarting.
cd "$(dirname "$0")" || exit 1
# Respect an existing MINIBREW_ENV_FILE (e.g. set by a host like Hermes) and only
# default to the repo-local .env when none is given.
export MINIBREW_ENV_FILE="${MINIBREW_ENV_FILE:-$PWD/.env}"
exec ./.venv/bin/mcp-server-minibrew "$@"
