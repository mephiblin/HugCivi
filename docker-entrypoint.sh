#!/bin/sh
set -eu

if [ "${GALLERY_DL_AUTO_UPDATE:-1}" = "1" ]; then
  echo "Updating gallery-dl package: ${GALLERY_DL_UPDATE_SPEC:-gallery-dl<2.0}"
  python -m pip install --no-cache-dir --upgrade "${GALLERY_DL_UPDATE_SPEC:-gallery-dl<2.0}" \
    || echo "WARNING: gallery-dl auto-update failed; continuing with bundled version."
fi

exec "$@"
