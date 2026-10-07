# Troubleshooting and recovery

## `probe` returns the version, then the mouse stops working

This is a confirmed behavior on the validated `MS1000V2.23H` mouse.

A successful probe looks like:

```text
firmware version: MS1000V2.23H
status: already at target MS1000V2.23
```

The query opens a C9 diagnostic session and closes the handles without sending the dangerous C9 commit. The firmware version is read correctly, but normal input can remain inactive afterward.

Restore normal input by toggling the mouse out of **Wired** mode and back. A cold cycle also works:

1. unplug USB-C;
2. toggle away from **Wired** and back to **Wired**;
3. wait about 10 seconds;
4. reconnect USB-C.

No firmware write is needed.

## Mouse enumerates after update but does not move

If `258a:0136` is present and Linux creates the normal HID/input devices, do the same cold mode cycle above before treating the update as failed.

This happened once during the initial Linux validation. After the cycle, both wired and 2.4 GHz operation worked normally.

An LED being off immediately after the update is not enough by itself to indicate failure; the update also changes LED configuration.

## Mouse is in `0603:1020`

Do not rerun the full flash immediately.

Check the state without writing anything:

```bash
sudo ./hansker-fw isp-status
```

Expected output:

```text
ISP bootloader 0603:1020: present at usb:BUS:DEVICE:0
raw USB enumeration: OK
```

If you know the Hansker is the device that just entered ISP mode, finish only Stage 2:

```bash
sudo ./hansker-fw recover-isp
```

Confirmation is:

```text
RECOVER HANSKER
```

`recover-isp` does not replay the C9/CB main-firmware stage.

### Why there may be no `/dev/hidraw*`

The `0603:1020` bootloader is control-only. Linux may log:

```text
usbhid ... couldn't find an input interrupt endpoint
```

and create no hidraw node. That is expected for this bootloader. The updater accesses it through raw USB/libusb control transfers.

### Important identity warning

`0603:1020` is a generic SINO WEALTH bootloader VID:PID and has no Hansker product string. Disconnect other devices that could be in the same bootloader before using `recover-isp`.

## Logs

`flash` and `recover-isp` write detailed logs under `~/Downloads/` by default. Preserve the log if an update stops unexpectedly.

Do not repeatedly retry destructive commands without first checking the current USB state with `lsusb` and `isp-status`.
