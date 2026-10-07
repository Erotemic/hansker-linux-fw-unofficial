import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hansker_updater import vendor


class TestVendorResources(unittest.TestCase):
    def test_import_from_extracted_directory_verifies_and_copies(self):
        firmware = b"synthetic firmware bytes"
        packets = b"synthetic packet bytes"
        fw_hash = hashlib.sha256(firmware).hexdigest()
        packet_hash = hashlib.sha256(packets).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "official-updater" / "Contents" / "Resources" / "bin"
            source.mkdir(parents=True)
            (source / vendor.FIRMWARE_FILENAME).write_bytes(firmware)
            (source / vendor.PACKETS_FILENAME).write_bytes(packets)
            destination = root / "local-vendor"

            with mock.patch.object(vendor, "FIRMWARE_SHA256", fw_hash), mock.patch.object(
                vendor, "PACKETS_SHA256", packet_hash
            ):
                result = vendor.import_vendor_resources(root / "official-updater", destination)

            self.assertEqual(result.destination, destination)
            self.assertEqual((destination / vendor.FIRMWARE_FILENAME).read_bytes(), firmware)
            self.assertEqual((destination / vendor.PACKETS_FILENAME).read_bytes(), packets)

    def test_bad_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / vendor.FIRMWARE_FILENAME).write_bytes(b"wrong")
            (root / vendor.PACKETS_FILENAME).write_bytes(b"wrong")
            with self.assertRaises(vendor.VendorResourceError):
                vendor.import_vendor_resources(root, root / "dest")

    def test_vendor_payloads_are_not_in_package_tree(self):
        package_root = Path(vendor.__file__).resolve().parent
        self.assertFalse((package_root / "resources" / vendor.FIRMWARE_FILENAME).exists())
        self.assertFalse((package_root / "resources" / vendor.PACKETS_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
