#!/usr/bin/env bash
# Rebuilds static/css/app.css from the Tailwind classes used in templates/, static/, app/.
# Run after adding or changing Tailwind classes; CI fails if the committed file is stale.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=v3.4.17
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ASSET=tailwindcss-macos-arm64; SHA=a1d0c7985759accca0bf12e51ac1dcbf0f6cf2fffb62e6e0f62d091c477a10a3 ;;
  Darwin-x86_64) ASSET=tailwindcss-macos-x64; SHA=6cbdad74be776c087ffa5e9a057512c54898f9fe8828d3362212dfe32fc933a3 ;;
  Linux-x86_64) ASSET=tailwindcss-linux-x64; SHA=7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4 ;;
  *) echo "Unsupported platform $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

BIN="${HOME}/.cache/spark-tailwind/${VERSION}/${ASSET}"
if [ ! -x "$BIN" ]; then
  mkdir -p "$(dirname "$BIN")"
  curl -sSfL -o "$BIN.tmp" "https://github.com/tailwindlabs/tailwindcss/releases/download/${VERSION}/${ASSET}"
  echo "${SHA}  ${BIN}.tmp" | shasum -a 256 -c - >/dev/null
  chmod +x "$BIN.tmp" && mv "$BIN.tmp" "$BIN"
fi

"$BIN" -c tailwind.config.js -i assets/tailwind.css -o static/css/app.css --minify
