# Safety model

The updater is intentionally conservative around the destructive parts of the firmware protocol.

## Device selection

A full flash:

- accepts only the validated Hansker Performance product string `Hansker Per` at `258a:0136`;
- rejects Hansker Productivity and unknown same-VID/PID products;
- rejects multiple matching Performance mice;
- checks for another process holding the vendor-control hidraw device;
- refuses a full flash if a `0603:1020` bootloader is already connected.

The last check matters because `0603:1020` is a generic SINO WEALTH bootloader identity and has no Hansker-specific product string.

## USB topology

`doctor` and `flash` require a direct USB connection by default. External or unknown topology is rejected unless explicitly overridden.

## Vendor resources

The repository does not contain Hansker firmware.

The importer authenticates the known official v2.23 DMG before extracting it and verifies both required resources by SHA-256. The updater rechecks those resource hashes before use.

See [vendor-resources.md](vendor-resources.md).

## Stage 1: main firmware

The C9/CB path:

- sends the C9 commit (`05 c9 70`) only after the complete CB firmware stream;
- requires the post-commit status to stabilize before ISP entry;
- treats an ambiguous commit result as a hard failure;
- uses the observed 8-second normal and 45-second post-commit timeout behavior.

A C9 session drop can be retried. A failure after destructive Stage 2 does not cause the entire main-firmware stage to be repeated automatically.

## Stage 2: ISP firmware

The ISP path:

- uses raw USB control transfers for the control-only `0603:1020` bootloader;
- writes all 14 secondary pages with the reset opcode disabled;
- restores page 0 only after the full first pass;
- reads the complete `0x7000` region back;
- requires a byte-for-byte match before enabling firmware or rebooting.

`recover-isp` skips Stage 1 entirely and refuses to start unless `0603:1020` is already present.

## Operator requirements

During `flash` or `recover-isp`:

- use a direct USB-C cable;
- do not disconnect or power-cycle the mouse;
- keep another pointing device available;
- preserve the generated log if the command exits unexpectedly.

If an operation stops unexpectedly, identify the current USB state before retrying. See [troubleshooting.md](troubleshooting.md).
