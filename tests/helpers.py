from __future__ import annotations

from hansker_updater import protocol as p


def synthetic_resources() -> p.Resources:
    firmware = bytearray((i * 17 + 3) & 0xFF for i in range(0x1F000))
    firmware[0] = 0x02

    packets = [p.Packet("feature", 5, 1, bytes.fromhex("05 00 00 00 00 00")) for _ in range(161)]
    packets[p.IDX_HEARTBEAT_0] = p.Packet("output", 1, 0, bytes.fromhex("01 01"))
    packets[p.IDX_C9_OPEN] = p.Packet("feature", 5, 1, bytes.fromhex("05 c9 60 00 00 00"))

    init = bytearray(1032)
    init[:4] = bytes.fromhex("06 c9 05 01")
    init[19:21] = bytes.fromhex("fb 4e")
    packets[p.IDX_C9_INIT] = p.Packet("feature", 6, 1, bytes(init))

    for index in range(p.IDX_CB_FIRST, p.IDX_CB_LAST + 1):
        marker = index.to_bytes(2, "little")
        data = bytes.fromhex("06 cb") + marker + bytes(28)
        packets[index] = p.Packet("feature", 6, 1, data)
    target = p.TARGET_VERSION.encode("ascii")
    cb0 = bytearray(packets[p.IDX_CB_FIRST].data)
    cb0[4 : 4 + len(target)] = target
    packets[p.IDX_CB_FIRST] = p.Packet("feature", 6, 1, bytes(cb0))

    packets[p.IDX_C9_COMMIT] = p.Packet("feature", 5, 1, bytes.fromhex("05 c9 70 00 00 00"))
    packets[p.IDX_POST_COMMIT] = p.Packet("feature", 6, 1, bytes.fromhex("06 c9 05 02 00 00"))
    packets[p.IDX_HEARTBEAT_1] = p.Packet("output", 1, 0, bytes.fromhex("01 01"))
    packets[p.IDX_ENTER_ISP] = p.Packet("feature", 5, 1, bytes.fromhex("05 75 00 00 00 00"))
    packets[p.IDX_ISP_HEARTBEAT] = p.Packet("output", 1, 0, bytes.fromhex("01 01"))
    packets[p.IDX_ISP_ERASE] = p.Packet("feature", 5, 0, bytes.fromhex("05 45 45 45 45 45"))
    packets[p.IDX_ISP_INIT_WRITE] = p.Packet("feature", 5, 0, bytes.fromhex("05 57 00 00 00 70"))

    secondary = bytes(firmware[: p.SECONDARY_SIZE])
    for page in range(p.SECONDARY_PAGES):
        start = page * p.PAGE_SIZE
        payload = bytearray(secondary[start : start + p.PAGE_SIZE])
        if page == 0:
            payload[0] = 0x00
        packets[p.IDX_ISP_PAGE_FIRST + page] = p.Packet(
            "feature", 6, 0, bytes.fromhex("06 77") + bytes(payload)
        )

    packets[p.IDX_ISP_REARM_WRITE] = p.Packet("feature", 5, 0, bytes.fromhex("05 57 00 00 00 70"))
    packets[p.IDX_ISP_REARM_PAGE0] = p.Packet(
        "feature", 6, 0, bytes.fromhex("06 77") + secondary[: p.PAGE_SIZE]
    )
    packets[p.IDX_ISP_INIT_READ] = p.Packet("feature", 5, 0, bytes.fromhex("05 52 00 00 00 70"))

    for index in range(p.IDX_EEPROM_FIRST, p.IDX_EEPROM_LAST + 1):
        packets[index] = p.Packet(
            "feature", 5, 0, bytes([0x05, 0x40, index & 0xFF, 0x00, 0x00, 0x00])
        )

    packets[p.IDX_ENABLE] = p.Packet("feature", 5, 0, bytes.fromhex("05 55 55 55 55 55"))
    packets[p.IDX_REBOOT] = p.Packet("feature", 5, 0, bytes.fromhex("05 5a 5a 5a 5a 5a"))
    packets[p.IDX_NORMAL_HEARTBEAT] = p.Packet("output", 1, 0, bytes.fromhex("01 01"))

    for index in range(p.IDX_LED_FIRST, p.IDX_LED_LAST + 1):
        data = bytearray(520)
        data[0] = 0x09
        data[1] = index - p.IDX_LED_FIRST + 1
        packets[index] = p.Packet("feature", 9, 1, bytes(data))

    packets[p.IDX_FINAL_HEARTBEAT] = p.Packet("output", 1, 0, bytes.fromhex("01 01"))
    return p.Resources(packets=tuple(packets), firmware=bytes(firmware))
