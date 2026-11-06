#!/bin/sh
set -e
PUID="${PUID:-99}"
PGID="${PGID:-100}"
mkdir -p "${CONFIG_DIR:-/config}"
if [ "$(id -u)" = "0" ]; then
  chown -R "$PUID:$PGID" "${CONFIG_DIR:-/config}" 2>/dev/null || true
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
