import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
COMPLETE_STATUSES = {"copied", "skipped_existing"}
ZERO_REQUIRED_FIELDS = ["missing_result", "missing_source", "conflict"]


def read_dict_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_dict_csv(path: Path, rows: List[Dict[str, str]], headers: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def int_value(value: object, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return default


def norm_path(path_text: str) -> str:
    return str(Path(path_text).resolve()).casefold()


def root_output_images(output_dir: Path) -> List[Path]:
    return sorted(
        [
            path
            for path in output_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ],
        key=lambda item: item.name.casefold(),
    )


def add_issue(
    issues: List[Dict[str, str]],
    severity: str,
    code: str,
    message: str,
    folder: str = "",
    path: str = "",
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "folder": folder,
            "path": path,
            "message": message,
        }
    )


def duplicate_values(values: Iterable[str]) -> Set[str]:
    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


def copied_manifest_targets(
    copied_path: Path,
    output_dir: Path,
    expected_count: int,
    issues: List[Dict[str, str]],
    folder: str,
) -> List[str]:
    rows = read_dict_csv(copied_path)
    if not copied_path.exists():
        add_issue(issues, "error", "missing_copied_manifest", "找不到 copied.csv。", folder, str(copied_path))
        return []
    if len(rows) != expected_count:
        add_issue(
            issues,
            "error",
            "copied_count_mismatch",
            f"copied.csv 筆數 {len(rows)} 不等於 folder_summary copied_count {expected_count}。",
            folder,
            str(copied_path),
        )

    targets: List[str] = []
    for row in rows:
        target_path = row.get("target_path") or ""
        if not target_path:
            add_issue(issues, "error", "missing_target_path", "copied.csv 有空白 target_path。", folder, str(copied_path))
            continue
        target = Path(target_path)
        targets.append(norm_path(target_path))
        if not target.exists():
            add_issue(issues, "error", "missing_target_file", "copied.csv 指向的輸出照片不存在。", folder, target_path)
        elif target.parent.resolve() != output_dir.resolve():
            add_issue(
                issues,
                "error",
                "target_not_in_flat_output",
                "copied.csv 指向的照片不在單一輸出資料夾第一層。",
                folder,
                target_path,
            )
    return targets


def audit_output(output_dir: Path) -> Tuple[Dict[str, object], List[Dict[str, str]]]:
    issues: List[Dict[str, str]] = []
    output_dir = output_dir.resolve()
    audit_dir = output_dir / "_ocr_audit"
    discovery_path = audit_dir / "folder_discovery.csv"
    unsupported_path = audit_dir / "skipped_unsupported.csv"
    summary_path = audit_dir / "folder_summary.csv"

    if not output_dir.exists():
        add_issue(issues, "error", "missing_output_dir", "找不到輸出資料夾。", path=str(output_dir))
        return {"output_dir": str(output_dir), "status": "failed"}, issues
    if not audit_dir.exists():
        add_issue(issues, "error", "missing_audit_dir", "找不到 _ocr_audit 資料夾。", path=str(audit_dir))
        return {"output_dir": str(output_dir), "status": "failed"}, issues

    for required in [discovery_path, unsupported_path, summary_path]:
        if not required.exists():
            add_issue(issues, "error", "missing_required_audit_file", "缺少必要審計檔。", path=str(required))

    discovery_rows = read_dict_csv(discovery_path)
    unsupported_rows = read_dict_csv(unsupported_path)
    summary_rows = read_dict_csv(summary_path)

    discovered_folders = [row.get("folder") or "" for row in discovery_rows if row.get("folder")]
    summary_folders = [row.get("folder") or "" for row in summary_rows if row.get("folder")]
    discovered_set = set(discovered_folders)
    summary_set = set(summary_folders)

    for folder in sorted(duplicate_values(discovered_folders)):
        add_issue(issues, "error", "duplicate_discovery_folder", "folder_discovery.csv 有重複資料夾。", folder)
    for folder in sorted(duplicate_values(summary_folders)):
        add_issue(issues, "error", "duplicate_summary_folder", "folder_summary.csv 有重複資料夾。", folder)
    for folder in sorted(discovered_set - summary_set):
        add_issue(issues, "error", "missing_summary_folder", "已發現含照片資料夾，但 folder_summary.csv 沒有完成紀錄。", folder)
    for folder in sorted(summary_set - discovered_set):
        add_issue(issues, "error", "extra_summary_folder", "folder_summary.csv 有不在 folder_discovery.csv 的資料夾。", folder)

    manifest_targets: List[str] = []
    copied_count_total = 0
    complete_count = 0
    for row in summary_rows:
        folder = row.get("folder") or ""
        status = row.get("status") or ""
        copied_count = int_value(row.get("copied_count"))
        copied_count_total += copied_count

        if status in COMPLETE_STATUSES:
            complete_count += 1
        else:
            add_issue(issues, "error", "incomplete_folder_status", f"資料夾狀態不是完成狀態：{status or '(空白)'}。", folder)

        for field in ZERO_REQUIRED_FIELDS:
            value = int_value(row.get(field))
            if value != 0:
                add_issue(issues, "error", field, f"{field} 必須為 0，目前是 {value}。", folder)

        if status in COMPLETE_STATUSES and copied_count <= 0:
            add_issue(issues, "error", "empty_copied_count", "完成狀態的資料夾 copied_count 不可為 0。", folder)

        copied_path_text = row.get("copied_path") or ""
        if status in COMPLETE_STATUSES:
            if not copied_path_text:
                add_issue(issues, "error", "missing_copied_path", "完成狀態缺少 copied_path。", folder)
            else:
                manifest_targets.extend(copied_manifest_targets(Path(copied_path_text), output_dir, copied_count, issues, folder))

        if status == "skipped_existing" and not row.get("source_latest_mtime"):
            add_issue(
                issues,
                "warning",
                "missing_source_latest_mtime",
                "續跑略過紀錄缺少 source_latest_mtime；舊版摘要可接受，但建議重跑接力器刷新摘要。",
                folder,
            )

    for target in sorted(duplicate_values(manifest_targets)):
        add_issue(issues, "error", "duplicate_manifest_target", "多個 copied.csv 指向同一個輸出照片。", path=target)

    output_images = root_output_images(output_dir)
    output_image_set = {norm_path(str(path)) for path in output_images}
    manifest_target_set = set(manifest_targets)
    for extra in sorted(output_image_set - manifest_target_set):
        add_issue(issues, "error", "extra_output_image", "輸出資料夾第一層有不在 copied.csv 的照片。", path=extra)
    for missing in sorted(manifest_target_set - output_image_set):
        add_issue(issues, "error", "missing_output_image", "copied.csv 的照片不在輸出資料夾第一層。", path=missing)

    if copied_count_total != len(output_images):
        add_issue(
            issues,
            "error",
            "flat_output_count_mismatch",
            f"folder_summary copied_count 加總 {copied_count_total} 不等於輸出資料夾第一層照片數 {len(output_images)}。",
            path=str(output_dir),
        )

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    summary: Dict[str, object] = {
        "output_dir": str(output_dir),
        "audit_dir": str(audit_dir),
        "status": "passed" if error_count == 0 else "failed",
        "folders_discovered": len(discovery_rows),
        "folders_summarized": len(summary_rows),
        "folders_complete": complete_count,
        "unsupported_files": len(unsupported_rows),
        "copied_count_total": copied_count_total,
        "flat_output_images": len(output_images),
        "errors": error_count,
        "warnings": warning_count,
    }
    return summary, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="驗收遞迴 OCR 單一輸出資料夾與 _ocr_audit 是否完整。")
    parser.add_argument("--output-dir", required=True, help="接力器輸出的單一資料夾")
    parser.add_argument("--no-write", action="store_true", help="只在終端機顯示結果，不寫 audit_report.csv")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    summary, issues = audit_output(output_dir)
    audit_dir = Path(str(summary.get("audit_dir") or output_dir / "_ocr_audit"))
    report_path = audit_dir / "audit_report.csv"

    if not args.no_write and audit_dir.exists():
        write_dict_csv(report_path, issues, ["severity", "code", "folder", "path", "message"])

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if issues:
        print(f"[驗收] status=failed issues={len(issues)} report={report_path}")
        for issue in issues[:20]:
            location = issue.get("folder") or issue.get("path") or ""
            print(f"[{issue['severity']}] {issue['code']} {location} {issue['message']}")
        if len(issues) > 20:
            print(f"[驗收] 其餘 {len(issues) - 20} 筆請看 {report_path}")
    else:
        print("[驗收] status=passed")

    return 0 if summary.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
