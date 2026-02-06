
import difflib
import json

# Mock Console
class MockConsole:
    def print(self, msg): pass
console = MockConsole()

# 1. Load Model List (Simulate reading the file)
valid_models_list = []
try:
    with open('型號表.txt', 'r', encoding='utf-8') as f:
        valid_models_list = [line.strip().upper() for line in f if line.strip()]
    print(f"Loaded {len(valid_models_list)} models.")
except Exception as e:
    print(f"Failed to load models: {e}")

# Test Cases
test_inputs = [
    "S27CG552EC",           # Perfect
    "MODEL: S27CG552EC",    # Noise 1
    "型號: S27CG552EC",     # Noise 2
    "S27CG552EC ",          # Trailing space
    "SAMSUNG S27CG552EC",   # Brand noise
    "MODEL:S27CG552EC"      # No space
]

print("-" * 50)
print(f"Target: S27CG552EC (Should be in list: {'S27CG552EC' in valid_models_list})")
print("-" * 50)

for raw_model in test_inputs:
    # --- Logic from samsung_ocr_batch_processor.py v18.91 ---
    clean_model = raw_model.strip().upper()
    noise_patterns = ['24"', '27"', '32"', '34"', '49"', '24INCH', '27INCH', '32INCH', 'SAMSUNG', 'HZ', 'MS', '1000R', '1500R', 'MODEL', '型號', ':', '：']
    for noise in noise_patterns:
        clean_model = clean_model.replace(noise, "")
    clean_model = clean_model.strip()
    
    in_list = clean_model in valid_models_list
    status = "✅ PASS" if in_list else "❌ FAIL"
    print(f"Input: '{raw_model}'\nCleaned: '{clean_model}'\nStatus: {status}\n")

