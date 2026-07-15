import os
import tempfile
import unittest
from pathlib import Path

from tools.source_inventory_snapshot import (
    CSV_NAME,
    SourceInventoryError,
    ensure_frozen_snapshot,
    folder_rows,
    stable_folder_id,
    verify_all,
    verify_folder,
)


class SourceInventorySnapshotTests(unittest.TestCase):
    def fixture(self, root: Path):
        source = root / "source"
        audit = root / "audit"
        folder = source / "2025" / "202512"
        folder.mkdir(parents=True)
        first = folder / "a.jpg"
        second = folder / "b.jpg"
        first.write_bytes(b"aaaa")
        second.write_bytes(b"bbbb")
        return source, audit, folder, first, second

    def test_stable_folder_id_does_not_depend_on_discovery_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, _audit, folder, _first, _second = self.fixture(Path(tmp))
            before = stable_folder_id(source, folder)
            (source / "2026" / "202601").mkdir(parents=True)
            after = stable_folder_id(source, folder)
            self.assertEqual(before, after)

    def test_preserved_size_and_mtime_content_change_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, audit, _folder, first, _second = self.fixture(Path(tmp))
            _summary, rows, _unsupported = ensure_frozen_snapshot(audit, source)
            original = first.stat()
            first.write_bytes(b"zzzz")
            os.utime(first, ns=(original.st_atime_ns, original.st_mtime_ns))
            errors = verify_all(source, rows)
            self.assertTrue(any("source_content_or_metadata_changed" in item for item in errors))

    def test_same_count_rename_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, audit, _folder, first, _second = self.fixture(Path(tmp))
            _summary, rows, _unsupported = ensure_frozen_snapshot(audit, source)
            first.rename(first.with_name("renamed.jpg"))
            errors = verify_all(source, rows)
            self.assertTrue(any("source_missing" in item for item in errors))
            self.assertTrue(any("source_added_or_renamed" in item for item in errors))

    def test_folder_verification_detects_non_latest_file_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, audit, _folder, first, second = self.fixture(Path(tmp))
            newer = second.stat().st_mtime_ns + 10_000_000_000
            os.utime(second, ns=(newer, newer))
            _summary, rows, _unsupported = ensure_frozen_snapshot(audit, source)
            first.write_bytes(b"changed-but-not-latest")
            identity = folder_rows(rows)[0]["folder_id"]
            errors = verify_folder(source, rows, identity)
            self.assertTrue(any("source_content_or_metadata_changed" in item for item in errors))

    def test_csv_tamper_and_existing_source_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, audit, _folder, _first, second = self.fixture(Path(tmp))
            ensure_frozen_snapshot(audit, source)
            with (audit / CSV_NAME).open("a", encoding="utf-8") as handle:
                handle.write("tampered\n")
            with self.assertRaisesRegex(SourceInventoryError, "csv_hash_mismatch"):
                ensure_frozen_snapshot(audit, source)

            # Rebuild a clean fixture and prove an existing snapshot never absorbs drift.
            other = Path(tmp) / "other"
            source2, audit2, _folder2, _first2, second2 = self.fixture(other)
            ensure_frozen_snapshot(audit2, source2)
            second2.write_bytes(b"different")
            with self.assertRaisesRegex(SourceInventoryError, "source_content_or_metadata_changed"):
                ensure_frozen_snapshot(audit2, source2)


if __name__ == "__main__":
    unittest.main()
