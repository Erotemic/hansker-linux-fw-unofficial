# Linux USB topology diagnostics

HIDAPI on Linux can identify the same USB interface using two path formats:

- **hidraw backend:** `/dev/hidrawN`, which can be looked up under `/sys/class/hidraw`.
- **libusb backend:** `3-4:1.1` (the format in [issue #1](https://github.com/Erotemic/hansker-linux-fw-unofficial/issues/1)), which can be looked up under `/sys/bus/usb/devices`.

The updater resolves either path to the USB interface in sysfs and verifies that
its parent USB device has the expected `258a:0136` identity. It then verifies
root-hub ancestry. Unknown or mismatched topology is **not** classified as
"direct". The check identifies intervening USB hubs, not the physical connector
shape (for example USB-A versus USB-C).

The process-opener check also handles both backends. With libusb paths it checks
the associated `/dev/bus/usb/BBB/DDD` device and any hidraw node belonging to
the selected interface. If it cannot map the libusb path to the device node,
`doctor` and `flash` refuse to continue rather than report no openers.

For diagnosis without flashing or sending firmware packets:

```bash
lsusb -t
readlink -f /sys/bus/usb/devices/3-4:1.1
cat /sys/bus/usb/devices/3-4/idVendor
cat /sys/bus/usb/devices/3-4/idProduct
sudo ./hansker-fw doctor
```

Replace `3-4:1.1` and `3-4` with the interface and device identifiers reported
on the affected machine. `doctor` does not write firmware. As before, actual
flashing has only been validated on the hardware documented in the README.
