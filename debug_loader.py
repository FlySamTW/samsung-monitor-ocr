
import os
import sys
import logging
import json

# Setup logging to console
logging.basicConfig(level=logging.INFO)

# Add skills folder to path
CMD_PWD = os.getcwd()
sys.path.append(os.path.join(CMD_PWD, 'skills'))

from skills.batch_orchestrator import BatchOrchestrator

IMAGE_DIR = r"d:\00_程式\20260120_商化自動OCR圖片\商化照片-202601"

config = {
    'image_dir': IMAGE_DIR,
    'output_dir': IMAGE_DIR,
    'assets_dir': os.path.join(CMD_PWD, 'assets'),
    'model_list_file': os.path.join(CMD_PWD, '型號表.txt')
}

print(f"Initializing Orchestrator for {IMAGE_DIR}...")
try:
    orchestrator = BatchOrchestrator(config)
    print("Orchestrator initialized.")
except Exception as e:
    print(f"Initialization failed: {e}")
    sys.exit(1)

print("Calling get_all_records()...")
try:
    records = orchestrator.get_all_records()
    print(f"Total Records Returned: {len(records)}")
except Exception as e:
    print(f"get_all_records failed: {e}")

# Check specifically for the big file content
big_file_path = os.path.join(IMAGE_DIR, "20260206-1925-OCR成功.json")
print(f"Checking big file via direct load: {big_file_path}")
if os.path.exists(big_file_path):
    print("Big file exists on disk.")
    try:
        with open(big_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"Big file has {len(data)} items raw.")
            
            # Check overlap
            loaded_files = set(r['file_name'] for r in records)
            missing = 0
            for item in data:
                img_path = item.get('data', {}).get('image', '')
                fname = os.path.basename(img_path)
                if fname not in loaded_files:
                    missing += 1
                    if missing < 5:
                        print(f"Missing Record: {fname}")
            print(f"Total Missing: {missing}")
            
    except Exception as e:
        print(f"Error reading big file directly: {e}")
else:
    print("Big file NOT found on disk!")
