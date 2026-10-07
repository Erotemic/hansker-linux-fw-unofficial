# Vendor firmware resources

This project does not redistribute Hansker firmware. The updater expects two files from Hansker's official v2.23 updater and verifies them before use.

## Download the official updater

Official release:

```text
https://github.com/hanskerdesign/firmware-updater/releases/tag/v2.23
```

Direct asset:

```bash
mkdir -p ~/Downloads
curl -fL --retry 3 --retry-delay 1 \
    https://github.com/hanskerdesign/firmware-updater/releases/download/v2.23/HanskerFirmwareUpdater.dmg \
    -o ~/Downloads/HanskerFirmwareUpdater.dmg
```

Expected DMG SHA-256:

```text
6bac5d6d0c00b2674accb3413752249d328f185197f24149467fd03e6ae1dbcc
```

Verify it:

```bash
printf '%s  %s\n' \
    6bac5d6d0c00b2674accb3413752249d328f185197f24149467fd03e6ae1dbcc \
    "$HOME/Downloads/HanskerFirmwareUpdater.dmg" \
    | sha256sum -c -
```

Or use the checked-in helper, which downloads to a temporary file and verifies the hash before replacing the destination:

```bash
./scripts/fetch-official-v2.23.sh
```

## Import automatically

Install 7-Zip on Ubuntu:

```bash
sudo apt install 7zip
```

Then import:

```bash
./hansker-fw import-resources ~/Downloads/HanskerFirmwareUpdater.dmg
```

The importer verifies the DMG before extraction, recursively finds the required resources, verifies them individually, and copies them to:

```text
.vendor/v2.23/firmware.bin
.vendor/v2.23/packets_performance.json
```

`.vendor/` is ignored by Git.

Expected resource hashes:

```text
b932769f06425db7243db17f7ba0464fe8bae6123e3881dcb897c9cf78b76ba4  firmware.bin
c4e867ec0b2a9ba32d7129ec560badd3bae2959f48b7d653036a793f1c7252ee  packets_performance.json
```

These values are also recorded in `VENDOR_SHA256SUMS` and checked whenever the updater loads the files.

## Manual extraction

To inspect the updater yourself:

```bash
rm -rf ~/Downloads/HanskerFirmwareUpdater-extracted
mkdir -p ~/Downloads/HanskerFirmwareUpdater-extracted

7zz x -y \
    -o"$HOME/Downloads/HanskerFirmwareUpdater-extracted" \
    "$HOME/Downloads/HanskerFirmwareUpdater.dmg"
```

Some systems provide `7z` instead of `7zz`.

Locate the two relevant files:

```bash
find ~/Downloads/HanskerFirmwareUpdater-extracted -type f \
    \( -name firmware.bin -o -name packets_performance.json \) \
    -print
```

Then import the extracted directory:

```bash
./hansker-fw import-resources \
    ~/Downloads/HanskerFirmwareUpdater-extracted
```

The importer searches recursively, so it does not depend on the macOS application bundle's internal path.

## Alternate resource directory

Set `HANSKER_FW_RESOURCE_DIR` before running the updater:

```bash
export HANSKER_FW_RESOURCE_DIR="$HOME/.local/share/hansker-linux-fw-unofficial/v2.23"
```

`packets_performance.json` contains main-firmware payload bytes and is treated as vendor firmware material, not as ordinary source data.
