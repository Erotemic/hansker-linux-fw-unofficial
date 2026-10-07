#!/usr/bin/env bash
set -euo pipefail

URL='https://github.com/hanskerdesign/firmware-updater/releases/download/v2.23/HanskerFirmwareUpdater.dmg'
SHA256='6bac5d6d0c00b2674accb3413752249d328f185197f24149467fd03e6ae1dbcc'
OUT="${1:-$HOME/Downloads/HanskerFirmwareUpdater.dmg}"

mkdir -p "$(dirname "$OUT")"

curl -fL --retry 3 --retry-delay 1 \
    "$URL" \
    -o "$OUT"

printf '%s  %s\n' "$SHA256" "$OUT" | sha256sum -c -

printf '\nDownloaded and verified Hansker v2.23 updater:\n  %s\n' "$OUT"
printf '\nImport the firmware resources with:\n  ./hansker-fw import-resources %q\n' "$OUT"
