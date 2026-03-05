
import os
import json
import unicodedata

IMAGE_DIR = r"d:\00_程式\20260120_商化自動OCR圖片\商化照片-202601"
JSON_PATH = os.path.join(IMAGE_DIR, "20260206-1925-OCR成功.json")

def dump_hex(s):
    return ":".join("{:04x}".format(ord(c)) for c in s)

print(f"Scanning {IMAGE_DIR}...")
try:
    actual_files = set(os.listdir(IMAGE_DIR))
    print(f"Found {len(actual_files)} files in directory.")
except Exception as e:
    print(f"Error listing directory: {e}")
    exit(1)

print(f"Loading {JSON_PATH}...")
try:
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f"Loaded {len(data)} records from JSON.")
except Exception as e:
    print(f"Error loading JSON: {e}")
    exit(1)

matches = 0
failures = 0

print("-" * 60)
print(f"{'JSON Filename':<40} | {'Status':<10} | {'Hex (JSON)':<20} | {'Hex (FS)'}")
print("-" * 60)

count = 0
for item in data:
    img_path = item.get('data', {}).get('image', '')
    fname = os.path.basename(img_path)
    
    # Check if exact match exists
    if fname in actual_files:
        matches += 1
    else:
        failures += 1
        # Try to find close match
        found = False
        for fs_fname in actual_files:
            if fs_fname == fname:
                found = True
                break
            if unicodedata.normalize('NFC', fs_fname) == unicodedata.normalize('NFC', fname):
                 print(f"MISMATCH (Normalization): {fname} != {fs_fname}")
                 print(f"JSON: {dump_hex(fname)}")
                 print(f"FS:   {dump_hex(fs_fname)}")
                 found = True
                 break
        
        if not found:
             # Just print first 5 failures to avoid spam
             if count < 5:
                 print(f"MISSING: {fname}")
                 print(f"JSON Hex: {dump_hex(fname)}")
             count += 1

print("-" * 60)
print(f"Total Records: {len(data)}")
print(f"Exact Matches: {matches}")
print(f"Failures:      {failures}")
