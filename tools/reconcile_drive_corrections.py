"""Explicit, idempotent Drive replacement reconciliation.

Dry-run is the default. Real work requires ``--execute --phase upload-new`` or
``--execute --phase trash-old``. The rclone runner is injectable for tests.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

STATUSES = {"detected", "new_ready", "new_uploaded_verified", "unchanged_remote_verified", "old_trash_pending", "old_trashed_verified"}
RCLONE_CALL_TIMEOUT_SECONDS = 180

def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def remote_md5(item: dict) -> str:
    hashes = item.get("Hashes") if isinstance(item.get("Hashes"), dict) else {}
    normalized_hashes = {str(key).lower(): value for key, value in hashes.items()}
    return str(
        normalized_hashes.get("md5")
        or item.get("MD5")
        or item.get("md5")
        or item.get("Hash")
        or ""
    ).strip().lower()

def remote_matches_receipt(item: dict, *, file_id: str, size: int, md5: str) -> bool:
    try: remote_size = int(item.get("Size", -1))
    except (TypeError, ValueError): return False
    return (
        str(item.get("ID") or "") == str(file_id or "")
        and remote_size == int(size)
        and remote_md5(item) == str(md5 or "").lower()
    )

def identity(row: dict[str, str]) -> str:
    original = (row.get("original_source_path") or row.get("source_path") or "").strip()
    period = (row.get("period") or row.get("year") or "").strip()
    content = (row.get("content_sha256") or row.get("source_sha256") or "").strip().lower()
    return "|".join((str(Path(original).resolve()).lower(), period, content))

def default_runner(rclone: str, args: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            [rclone, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RCLONE_CALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"rclone call timed out after {RCLONE_CALL_TIMEOUT_SECONDS}s"
    return p.returncode, p.stdout, p.stderr

class Reconciler:
    def __init__(self, ledger: Path, remote: str, rclone: str, execute: bool, runner: Callable = default_runner):
        self.ledger, self.remote, self.rclone, self.execute, self.runner = ledger, remote, rclone, execute, runner
        self.rows = self._load()

    def _load(self) -> list[dict]:
        if not self.ledger.exists(): return []
        return [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]

    def save(self) -> None:
        tmp = self.ledger.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in self.rows), encoding="utf-8")
        tmp.replace(self.ledger)

    def call(self, args: list[str]) -> tuple[int, str, str]:
        return self.runner(self.rclone, args)

    def ls(self, remote_path: str, *, missing_ok: bool = False) -> list[dict]:
        rc, out, err = self.call([
            "lsjson", f"{self.remote}:{remote_path}", "--files-only", "--hash-type", "MD5"
        ])
        if rc:
            missing_text = str(err or "").lower()
            if missing_ok and (
                "directory not found" in missing_text
                or "object not found" in missing_text
                or "file not found" in missing_text
            ):
                return []
            raise RuntimeError(f"lsjson failed: {err.strip()}")
        try: value = json.loads(out or "[]")
        except json.JSONDecodeError as exc: raise RuntimeError(f"invalid lsjson: {exc}") from exc
        return value if isinstance(value, list) else []

    def set_error(self, row: dict, error: str) -> None:
        row["last_error"] = error; row["last_error_at"] = datetime.now().isoformat(timespec="seconds")

    def discover_old(self, row: dict, dry_plan: bool = False) -> None:
        if row.get("old_drive_file_id"): return
        old_path = str(row.get("old_remote_path") or "")
        if not old_path: self.set_error(row, "old remote path missing"); return
        if dry_plan:
            row["planned_command"] = ["lsjson", f"{self.remote}:{old_path}", "--files-only", "--hash-type", "MD5"]
            return
        try: matches = self.ls(old_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if len(matches) != 1 or not str(matches[0].get("ID") or ""):
            self.set_error(row, "old remote path is missing or ambiguous"); return
        row["old_drive_file_id"] = str(matches[0]["ID"])
        row["old_id_discovery_receipt"] = "unique_path_readback"

    def upload_new(self, row: dict, dry_plan: bool = False) -> None:
        if row.get("status") in {"new_uploaded_verified", "unchanged_remote_verified", "old_trash_pending", "old_trashed_verified"}: return
        if row.get("status") != "new_ready":
            self.set_error(row, "upload requires status=new_ready")
            return
        local = Path(row.get("local_path") or row.get("source_path") or "")
        name = row.get("corrected_file_name") or local.name
        if not local.is_file(): self.set_error(row, "local corrected file missing"); return
        if row.get("gate_evidence"): self.set_error(row, "required gate evidence is not fresh/complete"); return
        year = row.get("year") or "_needs_review"; remote_path = f"{year}/{name}"
        size, md5 = local.stat().st_size, digest(local, "md5")
        if dry_plan:
            row["planned_command"] = ["lsjson", f"{self.remote}:{remote_path}", "--files-only", "then copyto --immutable and readback"]
            return
        try: matches = self.ls(remote_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if len(matches) > 1: self.set_error(row, "duplicate remote name"); return
        if matches and (int(matches[0].get("Size", -1)) != size or remote_md5(matches[0]) != md5):
            self.set_error(row, "remote name exists with size/hash mismatch"); return
        if matches:
            status = "unchanged_remote_verified" if row.get("old_remote_path") == remote_path else "new_uploaded_verified"
            row.update(status=status, new_drive_file_id=str(matches[0].get("ID", "")), new_remote_path=remote_path, new_remote_size=size, new_remote_md5=md5, new_upload_receipt="preexisting_hash_identical")
            return
        rc, out, err = self.call(["copyto", str(local), f"{self.remote}:{remote_path}", "--immutable", "--drive-use-trash"])
        if rc: self.set_error(row, f"copyto failed: {err.strip()}"); return
        try: after = self.ls(remote_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if len(after) != 1 or int(after[0].get("Size", -1)) != size or remote_md5(after[0]) != md5:
            self.set_error(row, "new upload readback size/hash mismatch"); return
        status = "unchanged_remote_verified" if row.get("old_remote_path") == remote_path else "new_uploaded_verified"
        row.update(status=status, new_drive_file_id=str(after[0].get("ID", "")), new_remote_path=remote_path, new_remote_size=size, new_remote_md5=md5, new_upload_receipt=out[-1000:])

    def trash_old(self, row: dict, dry_plan: bool = False) -> None:
        if row.get("status") in {"unchanged_remote_verified", "old_trashed_verified"}: return
        pending = row.get("status") == "old_trash_pending"
        if row.get("status") not in {"new_uploaded_verified", "old_trash_pending"}: self.set_error(row, "new upload is not verified"); return
        old_path, old_id, new_path = row.get("old_remote_path", ""), row.get("old_drive_file_id", ""), row.get("new_remote_path", "")
        if not old_id or not old_path or old_path == new_path: self.set_error(row, "old ID/path missing or paths equal"); return
        if dry_plan:
            row["planned_command"] = ["lsjson", f"{self.remote}:{old_path}", "then deletefile --drive-use-trash and readback"]; return
        try: old = self.ls(old_path, missing_ok=pending)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if pending and not old:
            try: new_after = self.ls(new_path)
            except RuntimeError as exc: self.set_error(row, str(exc)); return
            if len(new_after) != 1 or not remote_matches_receipt(
                new_after[0],
                file_id=str(row.get("new_drive_file_id") or ""),
                size=int(row.get("new_remote_size") or -1),
                md5=str(row.get("new_remote_md5") or ""),
            ):
                self.set_error(row, "pending trash recovery could not verify surviving new file"); return
            row.update(status="old_trashed_verified", old_disposal_receipt="readback_old_absent_after_pending")
            row.pop("last_error", None)
            row.pop("last_error_at", None)
            row.pop("planned_command", None)
            return
        if len(old) != 1 or str(old[0].get("ID", "")) != old_id: self.set_error(row, "old remote ID/path mismatch"); return
        if not pending:
            row["status"] = "old_trash_pending"; self.save()
        rc, out, err = self.call(["deletefile", f"{self.remote}:{old_path}", "--drive-use-trash"])
        if rc: self.set_error(row, f"trash failed: {err.strip()}"); return
        try: old_after, new_after = self.ls(old_path, missing_ok=True), self.ls(new_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if old_after or len(new_after) != 1 or not remote_matches_receipt(
            new_after[0],
            file_id=str(row.get("new_drive_file_id") or ""),
            size=int(row.get("new_remote_size") or -1),
            md5=str(row.get("new_remote_md5") or ""),
        ):
            self.set_error(row, "trash/readback verification failed"); return
        row.update(status="old_trashed_verified", old_disposal_receipt=out[-1000:])
        row.pop("last_error", None)
        row.pop("last_error_at", None)
        row.pop("planned_command", None)

    @staticmethod
    def _name_map(entries: list[dict]) -> dict[str, list[dict]]:
        mapped: dict[str, list[dict]] = {}
        for entry in entries:
            name = str(entry.get("Name") or entry.get("Path") or "").strip()
            if name:
                mapped.setdefault(name.replace("\\", "/").rsplit("/", 1)[-1], []).append(entry)
        return mapped

    def trash_old_batch(self, rows: list[dict], dry_plan: bool = False) -> None:
        """Dispose verified old names with one year listing before/after.

        Google Drive single-path lookups are slow on this workstation.  The
        correction ledger is flat by year, so one exact year snapshot supplies
        the same ID/hash authority without serially listing every old and new
        filename.  Deletion remains one explicit ID-bound path at a time.
        """
        if dry_plan:
            for row in rows:
                self.trash_old(row, dry_plan=True)
            return

        by_year: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("status") in {"unchanged_remote_verified", "old_trashed_verified"}:
                continue
            by_year.setdefault(str(row.get("year") or "_needs_review"), []).append(row)

        for year, year_rows in by_year.items():
            try:
                before = self._name_map(self.ls(year))
            except RuntimeError as exc:
                for row in year_rows:
                    self.set_error(row, str(exc))
                self.save()
                continue

            pending_verification: list[dict] = []
            for row in year_rows:
                pending = row.get("status") == "old_trash_pending"
                if row.get("status") not in {"new_uploaded_verified", "old_trash_pending"}:
                    self.set_error(row, "new upload is not verified")
                    self.save()
                    continue
                old_path = str(row.get("old_remote_path") or "")
                new_path = str(row.get("new_remote_path") or "")
                old_id = str(row.get("old_drive_file_id") or "")
                if not old_id or not old_path or old_path == new_path:
                    self.set_error(row, "old ID/path missing or paths equal")
                    self.save()
                    continue
                old_name = old_path.replace("\\", "/").rsplit("/", 1)[-1]
                new_name = new_path.replace("\\", "/").rsplit("/", 1)[-1]
                old_matches = before.get(old_name, [])
                new_matches = before.get(new_name, [])
                new_ok = len(new_matches) == 1 and remote_matches_receipt(
                    new_matches[0],
                    file_id=str(row.get("new_drive_file_id") or ""),
                    size=int(row.get("new_remote_size") or -1),
                    md5=str(row.get("new_remote_md5") or ""),
                )
                if pending and not old_matches and new_ok:
                    row.update(
                        status="old_trashed_verified",
                        old_disposal_receipt="year_snapshot_old_absent_after_pending",
                    )
                    row.pop("last_error", None)
                    row.pop("last_error_at", None)
                    self.save()
                    continue
                if len(old_matches) != 1 or str(old_matches[0].get("ID") or "") != old_id:
                    self.set_error(row, "old remote ID/path mismatch")
                    self.save()
                    continue
                if not new_ok:
                    self.set_error(row, "surviving new file does not match verified receipt")
                    self.save()
                    continue
                if not pending:
                    row["status"] = "old_trash_pending"
                    self.save()
                rc, out, err = self.call([
                    "deletefile", f"{self.remote}:{old_path}", "--drive-use-trash"
                ])
                if rc:
                    self.set_error(row, f"trash failed: {err.strip()}")
                    self.save()
                    continue
                row["old_disposal_command_receipt"] = out[-1000:]
                pending_verification.append(row)

            if not pending_verification:
                continue
            try:
                after = self._name_map(self.ls(year))
            except RuntimeError as exc:
                for row in pending_verification:
                    self.set_error(row, str(exc))
                self.save()
                continue
            for row in pending_verification:
                old_name = str(row.get("old_remote_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
                new_name = str(row.get("new_remote_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
                new_matches = after.get(new_name, [])
                if after.get(old_name) or len(new_matches) != 1 or not remote_matches_receipt(
                    new_matches[0],
                    file_id=str(row.get("new_drive_file_id") or ""),
                    size=int(row.get("new_remote_size") or -1),
                    md5=str(row.get("new_remote_md5") or ""),
                ):
                    self.set_error(row, "batch trash/readback verification failed")
                    self.save()
                    continue
                row.update(
                    status="old_trashed_verified",
                    old_disposal_receipt="year_snapshot_delete_and_readback_verified",
                )
                row.pop("last_error", None)
                row.pop("last_error_at", None)
                row.pop("planned_command", None)
                self.save()


def run_phase(rec: Reconciler, phase: str, *, dry_plan: bool) -> None:
    """Run one reconciliation phase only for row-level authorized work."""
    actionable = [row for row in rec.rows if row.get("status") != "detected"]
    if phase == "trash-old":
        rec.trash_old_batch(actionable, dry_plan=dry_plan)
        rec.save()
        return
    for row in rec.rows:
        # ``detected`` rows are an explicitly deferred legacy-cleanup backlog.
        # They are not row-level authorized corrections and must never consume
        # remote calls or delay the OCR/year handoff.
        if row.get("status") == "detected":
            continue
        if phase == "discover-old":
            rec.discover_old(row, dry_plan=dry_plan)
        else:
            rec.upload_new(row, dry_plan=dry_plan)
        # Persist each row boundary so a network/process interruption resumes
        # from the next correction instead of replaying the whole phase.
        rec.save()
    rec.save()


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output-dir",required=True); ap.add_argument("--ledger",default=""); ap.add_argument("--remote",default="samsung_ocr_drive"); ap.add_argument("--rclone",default="rclone"); ap.add_argument("--execute",action="store_true"); ap.add_argument("--phase",choices=("discover-old","upload-new","trash-old"),default="")
    args=ap.parse_args(); out=Path(args.output_dir).resolve(); md=out/"_drive_upload"; ledger=Path(args.ledger) if args.ledger else md/"drive_correction_reconciliation.jsonl"
    if args.execute and not args.phase: ap.error("--execute requires explicit --phase")
    rec=Reconciler(ledger,args.remote,args.rclone,args.execute)
    run_phase(rec, args.phase or "upload-new", dry_plan=not args.execute)
    print(json.dumps({"execute":args.execute,"phase":args.phase or "plan","rows":len(rec.rows),"status_counts":{s:sum(r.get("status")==s for r in rec.rows) for s in STATUSES}},ensure_ascii=False))
    return 0
if __name__ == "__main__": raise SystemExit(main())
