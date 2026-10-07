# Unofficial Hansker Performance Linux firmware updater

> **Unofficial community project.** This repository is not affiliated with, endorsed by, or supported by Hansker. Hansker and its product names are used only to identify compatible hardware.

Linux firmware updater for the **Hansker Performance / HK95** mouse (`258a:0136`), reconstructed from Hansker's official v2.23 updater.

The updater has been validated end-to-end on one physical Hansker Performance unit upgraded from `MS1000V2.07H` to `MS1000V2.23H` on Ubuntu Linux. The first Linux hardware run also exercised the `0603:1020` ISP recovery path and verified all `0x7000` bytes of the secondary image before rebooting.

This repository intentionally contains **no Hansker firmware or captured firmware payloads**. Users import the required resources locally from Hansker's official updater; the importer verifies known-good SHA-256 hashes before any firmware operation can run.

## What is tested

- Read-only version query on a physical `258a:0136` Hansker Performance.
- Full C9/CB main-firmware transfer and commit.
- Transition into the SINO WEALTH `0603:1020` ISP bootloader.
- Linux control-only ISP access through raw USB/libusb when `usbhid` creates no `hidraw` device.
- Fourteen 2048-byte secondary pages written and read back byte-for-byte before enable/reboot.
- Return to `258a:0136`; a one-time explicit post-flash probe reported `MS1000V2.23H`.
- Wired and 2.4 GHz mouse operation after a cold power-cycle/mode toggle.

The protocol is destructive and can brick the device if interrupted. Keep the USB-C cable connected throughout a flash or ISP recovery operation.

## Safety properties

The updater deliberately adds checks around the dangerous parts of the vendor protocol:

- Supports only the Performance/HK95 product string, not Productivity/HK25.
- Requires locally imported vendor resources to match known SHA-256 hashes.
- Never sends `05 c9 70` (commit) until every CB firmware packet has been sent.
- Requires the C9 commit status to stabilize for about 400 ms as either `0x00` (healthy) or `0xff` (session dropped). An ambiguous commit state aborts before ISP entry.
- Uses the observed 8 s packet timeout and 45 s post-commit timeout behavior.
- Writes all 14 secondary pages with the reset opcode disabled, then re-arms page 0 only after the complete write.
- Reads the entire `0x7000` secondary region back and compares it byte-for-byte before enabling or rebooting it.
- Uses raw USB control transfers for `0603:1020`; Linux may not create a `hidraw` node for this control-only bootloader.
- Checks for other userspace processes already holding the normal mouse control `hidraw` node.
- Refuses an external USB hub/adapter by default.
- Has a dedicated `recover-isp` path that refuses to replay Stage 1 when the device is already in `0603:1020`.

## Install

For a standalone environment:

```bash
uv sync
./scripts/test.sh
```

The `./hansker-fw` wrapper runs `.venv/bin/python -m hansker_updater`.

For Ubuntu 24.04, importing directly from the official macOS DMG also needs 7-Zip:

```bash
sudo apt install 7zip
```

## Import the official firmware resources

Download Hansker's official updater yourself. Then import the required files locally:

```bash
./hansker-fw import-resources ~/Downloads/HanskerFirmwareUpdater.dmg
```

In a source checkout this stores only the verified files under:

```text
.vendor/v2.23/firmware.bin
.vendor/v2.23/packets_performance.json
```

`.vendor/` is ignored by Git. The repository never needs to redistribute those files.

Known-good hashes are recorded in `VENDOR_SHA256SUMS`.

You can also import from an already-extracted updater directory:

```bash
./hansker-fw import-resources ~/Downloads/HanskerFirmwareUpdater-extracted
```

To use another local resource directory, set:

```bash
export HANSKER_FW_RESOURCE_DIR="$HOME/.local/share/hansker-linux-fw-unofficial/v2.23"
```

## Normal update

Run the offline tests first:

```bash
./scripts/test.sh
```

Preflight the connected mouse:

```bash
sudo ./hansker-fw doctor
```

Read the current firmware version without committing or entering ISP:

```bash
sudo ./hansker-fw probe
```

If you ran `probe`, unplug the mouse for at least 5 seconds and reconnect it directly before flashing so the abandoned C9 session is reset.

Then flash:

```bash
sudo ./hansker-fw flash
```

The full flash requires an explicit confirmation phrase and writes a log under `~/Downloads/`. By default it does **not** run another C9 version probe after flashing: that probe opens a fresh C9 session and may temporarily suppress normal input until the device is power-cycled. The successful destructive path instead finishes after secondary read-back verification, firmware enable/reboot, normal `258a:0136` re-enumeration, and post-flash configuration.

If you explicitly want the version string immediately afterward, use `--post-probe`, with the expectation that you may need to power-cycle afterward.

After a successful update, the firmware is verified and it is safe to disconnect the cable. On the first validated Linux update, HID enumeration returned immediately but mouse input remained silent until a cold mode/power cycle. If that happens:

1. Unplug USB-C.
2. Toggle the bottom switch away from **Wired**, then back to **Wired**.
3. Leave it disconnected for about 10 seconds.
4. Reconnect USB-C directly.

Do not interpret an LED being off immediately after update as a failure by itself; the update sequence also changes LED configuration.

## ISP recovery

If a flash reaches Stage 2 but the process exits while the mouse remains in the SINO WEALTH ISP bootloader, check the state without writing anything:

```bash
sudo ./hansker-fw isp-status
```

Expected recovery state:

```text
ISP bootloader 0603:1020: present at usb:BUS:DEVICE:0
raw USB enumeration: OK
```

Then finish **only Stage 2**:

```bash
sudo ./hansker-fw recover-isp
```

The confirmation phrase is deliberately short:

```text
RECOVER HANSKER
```

`recover-isp` refuses to run unless `0603:1020` is already present. It does not send C9 open, CB main-firmware packets, or the C9 commit. Like the normal flash path, it skips the final C9 version probe by default; add `--post-probe` only when you specifically want that diagnostic.

## Update pipeline

### Stage 1 — Hansker C9/CB protocol

Normal mouse: `258a:0136`, interface 1.

```text
05 c9 60 ...       C9 open
06 c9 05 01 ...    C9 init
06 cb ...           122 captured firmware packets
05 c9 70 ...       COMMIT — dangerous if CB data is incomplete
06 c9 05 02 ...    post-commit command / internal flash wait
05 75 ...          enter ISP
```

Report-5 status byte 2 behaves as:

```text
0x55  busy
0x00  ready / active session
0xff  idle / session dropped
```

The validated mouse exhibited the same C9-init stall described by the vendor updater: report 5 can remain unreadable for more than 8 seconds after init and the update can still proceed successfully.

### Stage 2 — SINO WEALTH ISP bootloader

The mouse re-enumerates as `0603:1020`, interface 0. On Linux this bootloader may be visible in `lsusb` while `usbhid` logs:

```text
couldn't find an input interrupt endpoint
```

and creates no `/dev/hidraw*`. Stage 2 therefore uses raw HID class `SET_REPORT` / `GET_REPORT` USB control transfers via PyUSB/libusb instead of relying on `hidapi` enumeration.

The updater writes and verifies a `0x7000`-byte secondary region as fourteen 2048-byte pages, applies the captured EEPROM configuration, enables firmware, reboots, waits for `258a:0136`, and sends the post-flash LED configuration.

## Vendor resources and licensing

The expected vendor resources are:

```text
firmware.bin
SHA256 b932769f06425db7243db17f7ba0464fe8bae6123e3881dcb897c9cf78b76ba4

packets_performance.json
SHA256 c4e867ec0b2a9ba32d7129ec560badd3bae2959f48b7d653036a793f1c7252ee
```

`packets_performance.json` contains main-firmware payload bytes, so it is treated as vendor firmware material and is not committed to this repository either.

The MIT license in `LICENSE` applies to this repository's community implementation and documentation, not to firmware or other material supplied by Hansker. See `THIRD_PARTY.md`.

## Reverse-engineering references

See `REVERSE_ENGINEERING.md` for the observed protocol behavior and packet sequence.

The standard SINO WEALTH ISP behavior was cross-checked against:

- https://github.com/carlossless/sinowisp
- https://github.com/carlossless/hidra
