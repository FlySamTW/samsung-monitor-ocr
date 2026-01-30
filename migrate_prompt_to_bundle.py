"""
Prompt 遷移腳本：將 txt 檔案遷移到 Bundle 系統
用途：將現有的 prompt txt 檔案轉換為版本化的 JSON bundle

執行方式：
    python migrate_prompt_to_bundle.py [--version v2.0]
"""

import os
import sys
import json
import shutil
from datetime import datetime
from pathlib import Path

# 加入 skills 路徑
sys.path.insert(0, str(Path(__file__).parent))

from skills.prompt_versioning import PromptManager

def backup_old_files():
    """備份舊的 txt 檔案到 backup 資料夾"""
    backup_dir = Path("backup")
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    files_to_backup = [
        "samsung_ocr_prompt.txt",
        "samsung_ocr_prompt_v2.txt",
        "samsung_ocr_prompt_v1_backup.txt"
    ]
    
    backed_up = []
    for fname in files_to_backup:
        if Path(fname).exists():
            backup_path = backup_dir / f"{Path(fname).stem}_{timestamp}.txt"
            shutil.copy(fname, backup_path)
            backed_up.append(f"{fname} → {backup_path}")
            print(f"✅ 備份: {fname} → {backup_path.name}")
    
    return backed_up

def migrate_to_bundle(version_name="v2.0_migrated"):
    """將 txt 檔案遷移到 Bundle 系統"""
    
    print("=" * 50)
    print("🔄 Prompt 遷移工具")
    print("=" * 50)
    print()
    
    # 1. 備份舊檔案
    print("[1/4] 備份現有檔案...")
    backed_up = backup_old_files()
    
    # 2. 讀取最新的 prompt
    print("\n[2/4] 讀取 Prompt 內容...")
    
    # 優先讀取 v2 版本，如果不存在則讀主檔案
    prompt_file = "samsung_ocr_prompt_v2.txt" if Path("samsung_ocr_prompt_v2.txt").exists() else "samsung_ocr_prompt.txt"
    
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            current_prompt = f.read()
        print(f"✅ 讀取成功: {prompt_file} ({len(current_prompt)} 字元)")
    except FileNotFoundError:
        print(f"❌ 找不到檔案: {prompt_file}")
        return False
    
    # 3. 建立 Bundle
    print("\n[3/4] 建立 Prompt Bundle...")
    
    pm = PromptManager("assets")
    
    # 檢測 prompt 版本（從內容中尋找版本標記）
    if "v2.0" in current_prompt or "Qwen3-VL 專用 v2.0" in current_prompt:
        detected_version = "v2.0"
    else:
        detected_version = "v1.0"
    
    bundle_data = {
        "version_id": f"prompt_{detected_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "created_at": datetime.now().isoformat(),
        "source_file": prompt_file,
        "system_prompt": current_prompt,
        "user_prompt_template": "請分析這張三星螢幕照片，提取型號與價格資訊。",
        "few_shot_config": {
            "source": "dynamic",
            "k": 1,
            "description": "從 AutoCurator 動態載入人工訂正的範例"
        },
        "parameters": {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": 1024
        },
        "metadata": {
            "migrated_at": datetime.now().isoformat(),
            "migrated_by": "migrate_prompt_to_bundle.py",
            "original_files_backed_up": backed_up
        }
    }
    
    version_id = pm.save_bundle(bundle_data)
    print(f"✅ Bundle 建立成功: {version_id}")
    
    # 4. 建立 latest 指標
    print("\n[4/4] 建立 latest 指標...")
    
    latest_path = Path("assets/prompt_bundles/latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({
            "latest_version": version_id,
            "updated_at": datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Latest 指標: {version_id}")
    
    # 5. 更新主檔案（從 v2 複製）
    if prompt_file == "samsung_ocr_prompt_v2.txt":
        print("\n[5/5] 更新主檔案...")
        shutil.copy("samsung_ocr_prompt_v2.txt", "samsung_ocr_prompt.txt")
        print("✅ samsung_ocr_prompt.txt 已更新為 v2.0 版本")
    
    print("\n" + "=" * 50)
    print("✅ 遷移完成！")
    print("=" * 50)
    print(f"\n📦 Bundle 位置: assets/prompt_bundles/{version_id}.json")
    print(f"💾 備份位置: backup/")
    print(f"\n⚠️ 下一步：重啟伺服器以載入新的 Bundle 系統")
    print("\n執行: run_ocr.bat")
    
    return True

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="遷移 Prompt 到 Bundle 系統")
    parser.add_argument("--version", default="v2.0_migrated", help="版本名稱")
    
    args = parser.parse_args()
    
    success = migrate_to_bundle(args.version)
    sys.exit(0 if success else 1)
