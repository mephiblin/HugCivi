#!/bin/sh
set -eu

PUID="${PUID:-0}"
PGID="${PGID:-0}"
UMASK="${UMASK:-022}"
umask "$UMASK"

mkdir -p /data /config

STARTUP_CONFIG_FILE="${HUGCIVI_STARTUP_CONFIG_FILE:-/config/startup.env}"
STARTUP_GALLERY_DL_AUTO_UPDATE="${GALLERY_DL_AUTO_UPDATE:-1}"
if [ -f "$STARTUP_CONFIG_FILE" ]; then
  CONFIGURED_GALLERY_DL_AUTO_UPDATE="$(
    awk -F= '$1 == "GALLERY_DL_AUTO_UPDATE" {print $2; exit}' "$STARTUP_CONFIG_FILE" 2>/dev/null \
      | tr -d '\r'
  )"
  case "$CONFIGURED_GALLERY_DL_AUTO_UPDATE" in
    0|1) STARTUP_GALLERY_DL_AUTO_UPDATE="$CONFIGURED_GALLERY_DL_AUTO_UPDATE" ;;
  esac
fi

if [ "$STARTUP_GALLERY_DL_AUTO_UPDATE" = "1" ]; then
  echo "Updating gallery-dl package: ${GALLERY_DL_UPDATE_SPEC:-gallery-dl<2.0}"
  python -m pip install --no-cache-dir --upgrade "${GALLERY_DL_UPDATE_SPEC:-gallery-dl<2.0}" \
    || echo "WARNING: gallery-dl auto-update failed; continuing with bundled version."
else
  echo "Skipping gallery-dl auto-update."
fi

python -m gallery_dl --version 2>/dev/null | sed 's/^/gallery-dl version: /' || true

if [ "$PUID" != "0" ] || [ "$PGID" != "0" ]; then
  if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" appgroup
  fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /config -s /usr/sbin/nologin appuser
  fi
  if [ "${HUGCIVI_CHOWN_ON_START:-0}" = "1" ]; then
    chown -R "$PUID:$PGID" /data /config
  fi
  exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
