# Third-party material and trademarks

This repository contains only the community implementation, tests, and
reverse-engineering notes. It intentionally does **not** redistribute Hansker
firmware, packet captures containing firmware bytes, the official updater, or
Hansker artwork.

Users obtain the required firmware resources from Hansker's official updater
and import them locally with `hansker-fw import-resources`. Those imported
files are kept under `.vendor/` in a source checkout and are ignored by Git.
They remain subject to whatever rights and terms apply to the vendor material.

The v2.23 source used for validation is Hansker's public GitHub release:

```text
https://github.com/hanskerdesign/firmware-updater/releases/tag/v2.23
asset: HanskerFirmwareUpdater.dmg
SHA256: 6bac5d6d0c00b2674accb3413752249d328f185197f24149467fd03e6ae1dbcc
```

Hansker and Hansker product names are used only to identify compatible
hardware. This project is not affiliated with, endorsed by, or supported by
Hansker.

The SINO WEALTH SH68F90 ISP behavior was cross-checked against the open-source
`carlossless/sinowisp` and `carlossless/hidra` projects. No third-party source
files from those projects are vendored here.
