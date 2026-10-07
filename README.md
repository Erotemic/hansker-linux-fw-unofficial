# Unofficial Hansker Linux firmware updater

> Unofficial community project. Not affiliated with, endorsed by, or supported by Hansker.

Linux firmware updater for the **Hansker Performance / HK95** mouse (`258a:0136`), targeting `MS1000V2.23`.

**Experimental:** validated on one physical HK95 upgraded from `MS1000V2.07H` to `MS1000V2.23H` on Ubuntu Linux. Firmware updates can brick hardware if interrupted.

## Quick start

Requires Python 3.11+, `uv`, libusb, and 7-Zip.

```bash
uv sync
sudo apt install 7zip libusb-1.0-0

./scripts/fetch-official-v2.23.sh
./hansker-fw import-resources ~/Downloads/HanskerFirmwareUpdater.dmg

./scripts/test.sh
sudo ./hansker-fw doctor
sudo ./hansker-fw flash
```

The repository does not include Hansker firmware. The importer verifies the official v2.23 DMG and extracted firmware resources by SHA-256.

## Commands

| Command | Purpose |
| --- | --- |
| `./hansker-fw import-resources PATH` | Import and verify firmware from the official updater |
| `./hansker-fw plan` | Show the update plan without touching USB |
| `sudo ./hansker-fw doctor` | Check resources, device selection, and USB topology |
| `sudo ./hansker-fw flash` | Perform the v2.23 update |
| `sudo ./hansker-fw probe` | Read the current firmware version |
| `sudo ./hansker-fw isp-status` | Check for the `0603:1020` ISP bootloader |
| `sudo ./hansker-fw recover-isp` | Finish Stage 2 when already in ISP mode |

## Important notes

- Use a direct USB-C connection. Do not disconnect the mouse during `flash` or `recover-isp`.
- `probe` is non-committing, but on the validated `MS1000V2.23H` mouse it stops normal input until the connection mode is toggled or the mouse is cold-cycled.
- After an update, if the mouse enumerates but does not move, unplug it, toggle away from **Wired** and back, wait about 10 seconds, then reconnect.
- `0603:1020` is a generic SINO WEALTH bootloader ID. Use `recover-isp` only when you know the Hansker is the device in ISP mode.

## Documentation

- [Firmware resources](docs/vendor-resources.md) — official download, hashes, and manual extraction.
- [Safety model](docs/safety.md) — checks around destructive operations.
- [Troubleshooting](docs/troubleshooting.md) — probe behavior, post-update reset, and ISP recovery.
- [Reverse engineering](docs/reverse-engineering.md) — packet flow, timing, USB transport, and hardware observations.
- [Third-party material](THIRD_PARTY.md) — firmware/licensing boundary and trademarks.

## Development

```bash
uv sync --locked
./scripts/test.sh
```

Tests are offline and do not require a mouse or vendor firmware.

## License

MIT for this project's code and documentation. Hansker firmware and other vendor material are not included or licensed by this repository. See [THIRD_PARTY.md](THIRD_PARTY.md).
