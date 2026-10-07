# Reverse-engineering notes

These notes document the protocol behavior observed while building the **unofficial** Linux updater. They are not vendor documentation.

## Hardware and firmware

Validated device:

```text
Hansker Performance / HK95
normal VID:PID: 258a:0136
normal interface 0: mouse HID
normal interface 1: vendor feature reports 5, 6, and 9
ISP VID:PID: 0603:1020
```

Observed firmware before update:

```text
MS1000V2.07H
```

Observed firmware after the successful Linux update:

```text
MS1000V2.23H
```

The USB descriptor still reports `bcdDevice=2.05`; it is not the running firmware version.

## Non-committing version query

The generic SINO WEALTH `05 01 ...` query is not sufficient on this mouse. The updater's working query is:

```text
SET_FEATURE R5: 05 c9 60 00 00 00
wait for non-busy R5
SET_FEATURE R6: captured 1032-byte C9 init packet
wait up to 15 s for non-busy R5, but timeout is non-fatal
GET_FEATURE R6, 32 bytes
search response for MS1000V...
close handles WITHOUT C9 commit
```

Real Linux responses included:

```text
R6: 05 01 4d 53 31 30 30 30 56 32 2e 30 37 48 01 02 55 e1 74
firmware version: MS1000V2.07H
```

and after update:

```text
R6: 05 01 4d 53 31 30 30 30 56 32 2e 32 33 48 01 02 55 e3 32
firmware version: MS1000V2.23H
```

The query intentionally never sends `05 c9 70` and never sends ISP-enter `05 75`.

### Confirmed probe side effect on v2.23H

After the successful update, a second explicit Linux probe again returned
`MS1000V2.23H`. Immediately afterward, the mouse remained enumerated as
`258a:0136` but emitted no normal input reports. Toggling connection modes
restored both wireless and wired operation without another firmware write.

Therefore `probe` is non-committing but **not operationally side-effect-free** on
the validated unit. Treat it as a diagnostic that may temporarily disconnect
mouse input. After probing, toggle away from Wired and back (or perform a cold
power-cycle) before normal use or before starting a firmware update.

## C9/CB readiness behavior

`wait_for_ready_byte` behavior recovered from the updater implementation:

- GET_FEATURE report 5, length 6.
- `R5[2] == 0x55`: busy; continue polling.
- any other status byte: return immediately.
- poll interval about 2 ms.
- ordinary replay timeout 8 s.
- post-commit timeout 45 s.

`wait_for_stable_ready` is used to classify commit health:

- total timeout about 4 s.
- poll interval about 5 ms.
- candidate must be non-busy.
- the same status must remain stable for about 400 ms.
- stable `0x00`: session healthy and main firmware committed.
- stable `0xff`: session dropped; main firmware not committed.

The Linux implementation aborts before ISP entry if the commit result is ambiguous.

## Successful physical Stage 1 run

The first full Linux update on hardware produced:

```text
C9 open                         OK
C9 init                         R5 unreadable for >8 s; continued
CB firmware packets             122/122
C9 commit                       sent only after complete CB stream
post-commit stable status       0x00 for >400 ms
post-commit wait                R5 unreadable through the 45 s window
ISP-enter                       sent
```

The device then changed from `258a:0136` to `0603:1020` about one second after ISP-enter.

## Linux control-only ISP bootloader gotcha

`0603:1020` is a generic SINO WEALTH ISP identity and does not carry a Hansker-specific product string. The full updater therefore refuses to begin if such a bootloader is already present before Stage 1. This prevents a pre-existing unrelated ISP device from being mistaken for the Hansker after re-enumeration. Stage-2-only recovery is intentionally explicit and should be used only when the operator knows the Hansker is the device that entered ISP mode.


The first implementation incorrectly searched for `0603:1020` through `hidapi`. The device was actually present, but Linux logged:

```text
usb ... New USB device found, idVendor=0603, idProduct=1020
usbhid ... couldn't find an input interrupt endpoint
```

No `hidraw` node was created, so HID enumeration produced a false negative.

The corrected implementation discovers `0603:1020` through libusb/PyUSB and sends HID feature reports as raw class/interface control transfers:

```text
SET_REPORT Feature: bmRequestType=0x21, bRequest=0x09
GET_REPORT Feature: bmRequestType=0xa1, bRequest=0x01
wValue=(3 << 8) | report_id
wIndex=0
```

This behavior was cross-checked against the raw-USB backend in `carlossless/hidra` and the ISP protocol in `carlossless/sinowisp`.

## Secondary payload relation

The first 14 captured ISP write packets contain exactly `0x7000` bytes. Concatenating their data payloads differs from `firmware.bin[:0x7000]` at exactly one byte:

```text
offset 0x0000: first pass = 00, firmware.bin = 02
```

The capture then initializes a second write and retransmits page 0 with the original `0x02`. This keeps the reset vector disabled until the full secondary image has been transferred.

## Secondary sequence

Observed packet positions in the Performance capture:

```text
130  05 45 ...          erase
131  05 57 00 00 00 70 init write, 0x7000 bytes
132-145  06 77 ...      14 pages, first byte of page 0 cleared
146  05 57 00 00 00 70 re-init write
147  06 77 ...          page 0 with byte 0 restored to 02
148  05 52 00 00 00 70 init read for verification
149-153                 fixed 5-packet EEPROM configuration
154  05 55 ...          enable firmware
155  05 5a ...          reboot
157-159  report 9       post-reboot LED configuration
```

Verification responses use report 6 with transfer-direction byte `response[1] == 0x72`; the following 2048 bytes are page data.

## Successful physical ISP recovery

Because the original Linux detector falsely reported that `0603:1020` never appeared, the first full command exited with the mouse already sitting in ISP mode. A Stage-2-only recovery was then run through raw USB:

```text
erase secondary flash                          OK
write 14 x 2048-byte pages                     OK
re-arm page 0                                  OK
read back all 28672 bytes                      OK
byte-for-byte verification                     OK
EEPROM LED auto-off configuration              OK
enable firmware                                OK
reboot                                         OK
normal 258a:0136 enumeration                   OK
post-flash LED configuration                   OK
one-time explicit post-flash version query        MS1000V2.23H
```

This is why `recover-isp` exists and why it refuses to run unless `0603:1020` is already present. It skips the C9/CB main-firmware stage entirely.

## Post-flash version probe caveat

The first hardware validation ran a C9 version probe immediately after the successful Stage-2 reboot. That probe confirmed `MS1000V2.23H`, but it also opens a new C9 session and intentionally closes the handles without sending the dangerous commit command. Because the device relies on its own session-idle behavior to abandon that query session, the current updater does **not** run this probe automatically after a successful flash.

The default completion criterion is therefore: verified secondary read-back, firmware enable/reboot, return of the normal `258a:0136` device, and completion of post-flash configuration. `--post-probe` is available as an explicit diagnostic and may require a power-cycle afterward.

## Post-update input initialization

Immediately after the successful reboot, Linux created the normal mouse input node and HID interfaces, but the device emitted no `evtest` motion/button events and its LED was off.

A cold physical mode/power cycle resolved this:

1. unplug USB-C;
2. toggle away from **Wired** and back to **Wired**;
3. leave it disconnected for about 10 seconds;
4. reconnect USB-C.

After that, both wireless and wired operation worked normally.

This is treated as a post-update initialization quirk rather than a flash failure because:

- secondary flash had verified byte-for-byte;
- the normal `258a:0136` device had returned;
- the firmware version query returned `MS1000V2.23H`;
- HID descriptors/input nodes were present before the toggle;
- normal input resumed after the cold mode toggle without any further firmware write.

## Vendor resource boundary

The two required v2.23 resources are not published in this repository:

```text
firmware.bin
packets_performance.json
```

The packet capture contains main-firmware bytes, so both are treated as vendor firmware material. Users extract them locally from Hansker's official updater. The importer verifies the known SHA-256 values in `VENDOR_SHA256SUMS` before the updater can use them.

The v2.23 DMG used for the validated update came from Hansker's public GitHub
release asset:

```text
https://github.com/hanskerdesign/firmware-updater/releases/download/v2.23/HanskerFirmwareUpdater.dmg
SHA256 6bac5d6d0c00b2674accb3413752249d328f185197f24149467fd03e6ae1dbcc
```

On Linux, `7zz x HanskerFirmwareUpdater.dmg` exposes the application bundle.
The required `firmware.bin` and `packets_performance.json` are located
recursively by the importer rather than relying on a fixed bundle path.
