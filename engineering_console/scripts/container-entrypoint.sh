#!/bin/sh
set -eu

require_absolute_dir() {
  name="$1"
  eval "value=\${$name:-}"
  case "$value" in
    /*) ;;
    *) echo "Engineering Console disabled: $name must be an absolute path" >&2; exit 64 ;;
  esac
  mkdir -p "$value"
}

prepare_worker_repository() {
  require_absolute_dir INQSI_ENGINEERING_DATA_DIR
  require_absolute_dir INQSI_ENGINEERING_WORKSPACE_ROOT

  repo="${INQSI_ENGINEERING_REPOSITORY:-}"
  case "$repo" in
    /*) ;;
    *) echo "Engineering Console disabled: INQSI_ENGINEERING_REPOSITORY must be an absolute path" >&2; exit 64 ;;
  esac

  parent=$(dirname "$repo")
  mkdir -p "$parent"
  if [ ! -d "$repo/.git" ]; then
    if [ -e "$repo" ]; then
      echo "Engineering Console disabled: repository path exists but is not a git checkout" >&2
      exit 64
    fi
    tmp="${repo}.init.$$"
    rm -rf "$tmp"
    git clone --branch main --single-branch https://github.com/KirtKurt/parlay-platform.git "$tmp"
    mv "$tmp" "$repo"
  fi

  origin=$(git -C "$repo" remote get-url origin)
  case "$origin" in
    https://github.com/KirtKurt/parlay-platform|https://github.com/KirtKurt/parlay-platform.git) ;;
    *) echo "Engineering Console disabled: unexpected repository origin" >&2; exit 64 ;;
  esac

  branch=$(git -C "$repo" branch --show-current)
  if [ "$branch" != "main" ]; then
    echo "Engineering Console disabled: durable repository is not on main" >&2
    exit 64
  fi
  git -C "$repo" fetch --prune origin main
  git -C "$repo" merge --ff-only origin/main
}

case "${INQSI_ENGINEERING_ROLE:-}" in
  worker)
    prepare_worker_repository
    exec npm start
    ;;
  publisher)
    require_absolute_dir INQSI_ENGINEERING_DATA_DIR
    require_absolute_dir INQSI_ENGINEERING_PUBLISHER_LOCK_DIR
    interval="${INQSI_ENGINEERING_PUBLISH_INTERVAL_SECONDS:-15}"
    case "$interval" in
      ''|*[!0-9]*) echo "Engineering Console disabled: invalid publisher interval" >&2; exit 64 ;;
    esac
    if [ "$interval" -lt 5 ] || [ "$interval" -gt 300 ]; then
      echo "Engineering Console disabled: publisher interval must be 5-300 seconds" >&2
      exit 64
    fi
    while :; do
      if npm run publish; then :; else
        code=$?
        echo "Engineering Console publisher iteration failed (exit $code); retrying" >&2
      fi
      sleep "$interval"
    done
    ;;
  *)
    echo "Engineering Console disabled: INQSI_ENGINEERING_ROLE must be worker or publisher" >&2
    exit 64
    ;;
esac
