#!/bin/sh
set -eu

REPOSITORY="${INQSI_ENGINEERING_REPOSITORY:-/var/lib/inqsi/repository}"
DATA_DIR="${INQSI_ENGINEERING_DATA_DIR:-/var/lib/inqsi/data}"
WORKSPACE_ROOT="${INQSI_ENGINEERING_WORKSPACE_ROOT:-/var/lib/inqsi/workspaces}"

mkdir -p "$DATA_DIR" "$WORKSPACE_ROOT"

if [ ! -d "$REPOSITORY/.git" ]; then
  mkdir -p "$(dirname "$REPOSITORY")"
  git clone --branch main --single-branch https://github.com/KirtKurt/parlay-platform.git "$REPOSITORY"
else
  git -C "$REPOSITORY" fetch --prune origin main
  git -C "$REPOSITORY" checkout main
  git -C "$REPOSITORY" merge --ff-only origin/main
fi

exec node /app/src/server.js
