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

case "${INQSI_ENGINEERING_ROLE:-}" in
  worker)
    exec npm start
    ;;
  broker)
    exec node /app/scripts/start-broker.mjs
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
    echo "Engineering Console disabled: INQSI_ENGINEERING_ROLE must be worker, broker or publisher" >&2
    exit 64
    ;;
esac
