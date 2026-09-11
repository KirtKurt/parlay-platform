#!/bin/sh
set -eu

case "${INQSI_ENGINEERING_ROLE:-}" in
  worker)
    exec npm start
    ;;
  publisher)
    exec npm run publish
    ;;
  *)
    echo "Engineering Console disabled: INQSI_ENGINEERING_ROLE must be worker or publisher" >&2
    exit 64
    ;;
esac
