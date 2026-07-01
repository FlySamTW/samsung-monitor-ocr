import argparse
from pathlib import Path

from recursive_ocr_flat_export import validate_source_output_paths


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
    print(f"[預檢] OK source={source_root}")
    print(f"[預檢] OK output={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
