"""Explicit, idempotent Drive replacement reconciliation.

Dry-run is the default. Real work requires ``--execute --phase upload-new`` or
``--execute --phase trash-old``. The rclone runner is injectable for tests.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

STATUSES = {"detected", "new_ready", "new_uploaded_verified", "old_trash_pending", "old_trashed_verified"}

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
    return str(hashes.get("MD5") or item.get("MD5") or item.get("Hash") or "").strip().lower()

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
    p = subprocess.run([rclone, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
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

    def ls(self, remote_path: str) -> list[dict]:
        rc, out, err = self.call([
            "lsjson", f"{self.remote}:{remote_path}", "--files-only", "--hash-type", "MD5"
        ])
        if rc: raise RuntimeError(f"lsjson failed: {err.strip()}")
        try: value = json.loads(out or "[]")
        except json.JSONDecodeError as exc: raise RuntimeError(f"invalid lsjson: {exc}") from exc
        return value if isinstance(value, list) else []

    def set_error(self, row: dict, error: str) -> None:
        row["last_error"] = error; row["last_error_at"] = datetime.now().isoformat(timespec="seconds")

    def upload_new(self, row: dict, dry_plan: bool = False) -> None:
        if row.get("status") in {"new_uploaded_verified", "old_trash_pending", "old_trashed_verified"}: return
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
            row.update(status="new_uploaded_verified", new_drive_file_id=str(matches[0].get("ID", "")), new_remote_path=remote_path, new_remote_size=size, new_remote_md5=md5, new_upload_receipt="preexisting_hash_identical")
            return
        rc, out, err = self.call(["copyto", str(local), f"{self.remote}:{remote_path}", "--immutable", "--drive-use-trash"])
        if rc: self.set_error(row, f"copyto failed: {err.strip()}"); return
        try: after = self.ls(remote_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if len(after) != 1 or int(after[0].get("Size", -1)) != size or remote_md5(after[0]) != md5:
            self.set_error(row, "new upload readback size/hash mismatch"); return
        row.update(status="new_uploaded_verified", new_drive_file_id=str(after[0].get("ID", "")), new_remote_path=remote_path, new_remote_size=size, new_remote_md5=md5, new_upload_receipt=out[-1000:])

    def trash_old(self, row: dict, dry_plan: bool = False) -> None:
        if row.get("status") == "old_trashed_verified": return
        pending = row.get("status") == "old_trash_pending"
        if row.get("status") not in {"new_uploaded_verified", "old_trash_pending"}: self.set_error(row, "new upload is not verified"); return
        old_path, old_id, new_path = row.get("old_remote_path", ""), row.get("old_drive_file_id", ""), row.get("new_remote_path", "")
        if not old_id or not old_path or old_path == new_path: self.set_error(row, "old ID/path missing or paths equal"); return
        if dry_plan:
            row["planned_command"] = ["lsjson", f"{self.remote}:{old_path}", "then deletefile --drive-use-trash and readback"]; return
        try: old = self.ls(old_path)
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
            return
        if len(old) != 1 or str(old[0].get("ID", "")) != old_id: self.set_error(row, "old remote ID/path mismatch"); return
        if not pending:
            row["status"] = "old_trash_pending"; self.save()
        rc, out, err = self.call(["deletefile", f"{self.remote}:{old_path}", "--drive-use-trash"])
        if rc: self.set_error(row, f"trash failed: {err.strip()}"); return
        try: old_after, new_after = self.ls(old_path), self.ls(new_path)
        except RuntimeError as exc: self.set_error(row, str(exc)); return
        if old_after or len(new_after) != 1 or not remote_matches_receipt(
            new_after[0],
            file_id=str(row.get("new_drive_file_id") or ""),
            size=int(row.get("new_remote_size") or -1),
            md5=str(row.get("new_remote_md5") or ""),
        ):
            self.set_error(row, "trash/readback verification failed"); return
        row.update(status="old_trashed_verified", old_disposal_receipt=out[-1000:])

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output-dir",required=True); ap.add_argument("--ledger",default=""); ap.add_argument("--remote",default="samsung_ocr_drive"); ap.add_argument("--rclone",default="rclone"); ap.add_argument("--execute",action="store_true"); ap.add_argument("--phase",choices=("upload-new","trash-old"),default="")
    args=ap.parse_args(); out=Path(args.output_dir).resolve(); md=out/"_drive_upload"; ledger=Path(args.ledger) if args.ledger else md/"drive_correction_reconciliation.jsonl"
    if args.execute and not args.phase: ap.error("--execute requires explicit --phase")
    rec=Reconciler(ledger,args.remote,args.rclone,args.execute)
    for row in rec.rows:
        if args.phase == "trash-old": rec.trash_old(row, dry_plan=not args.execute)
        else: rec.upload_new(row, dry_plan=not args.execute)
    rec.save()
    print(json.dumps({"execute":args.execute,"phase":args.phase or "plan","rows":len(rec.rows),"status_counts":{s:sum(r.get("status")==s for r in rec.rows) for s in STATUSES}},ensure_ascii=False))
    return 0
if __name__ == "__main__": raise SystemExit(main())
