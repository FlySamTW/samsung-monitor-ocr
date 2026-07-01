import argparse
import os
from pathlib import Path
from typing import List, Optional, Tuple

from recursive_ocr_flat_export import IMAGE_EXTENSIONS, UNSUPPORTED_EXTENSIONS, validate_source_output_paths


def find_supported_image(source_root: Path) -> Tuple[Optional[Path], int]:
    unsupported_count = 0
    for current, _, filenames in os.walk(source_root):
        folder = Path(current)
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                return folder / name, unsupported_count
            if suffix in UNSUPPORTED_EXTENSIONS:
                unsupported_count += 1
    return None, unsupported_count


def root_output_images(output_dir: Path) -> List[Path]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    return [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="預檢遞迴 OCR 的來源與輸出路徑，不啟動 LLM 或 OCR 後端。")
    parser.add_argument("--source-root", required=True, help="要遞迴處理的照片根資料夾")
    parser.add_argument("--output-dir", required=True, help="改名後照片輸出的單一資料夾")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not source_root.exists():
        raise SystemExit(f"來源資料夾不存在：{source_root}")
    if not source_root.is_dir():
        raise SystemExit(f"來源路徑不是資料夾：{source_root}")

    validate_source_output_paths(source_root, output_dir)
    if output_dir.exists() and not output_dir.is_dir():
        raise SystemExit(f"輸出路徑已存在但不是資料夾：{output_dir}")

    existing_output_images = root_output_images(output_dir)
    summary_path = output_dir / "_ocr_audit" / "folder_summary.csv"
    if existing_output_images and not summary_path.exists():
        raise SystemExit(
            f"輸出資料夾第一層已有 {len(existing_output_images)} 張 jpg/jpeg/png，"
            "但找不到 _ocr_audit\\folder_summary.csv；請改用新的輸出資料夾，或先移開既有照片。"
        )

    supported_image, unsupported_count = find_supported_image(source_root)
    if not supported_image:
        if unsupported_count:
            raise SystemExit(
                f"來源資料夾沒有可處理的 jpg/jpeg/png 照片；找到 {unsupported_count} 個 HEIC/WebP，依目前規格會略過。"
            )
        raise SystemExit("來源資料夾沒有可處理的 jpg/jpeg/png 照片；請確認是否選到正確的照片根資料夾。")

    print(f"[預檢] OK source={source_root}")
    print(f"[預檢] OK output={output_dir}")
    print(f"[預檢] OK supported_image={supported_image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
